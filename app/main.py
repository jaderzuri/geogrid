
"""GeoGrid ZURI - Web UI and JSON API.

Run:
    uvicorn app.main:app --reload

Open:
    http://localhost:8000
"""

import asyncio
import os
import logging

from contextlib import asynccontextmanager
from html import escape
from functools import partial
from pathlib import Path
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config.branding import BRANDING, get_print_report_sections
from .competitive_metrics import (
    classify_competitors,
    keyword_competition_v2,
    position_opportunity,
    target_brand_confidence,
)
from .interpretation import generate_full_interpretation
from .fetchers.base import FetchError
from .business_finder import router as business_finder_router
from .commercial import landing_diagnostics_gate
from .commercial_api import _require_admin_session, _require_secure_admin_request
from .commercial_api import router as commercial_router
from .commercial_api import wordpress_router
from .external_api import ExternalRequestSizeLimitMiddleware, router as external_router
from .grid import validate_grid
from .landing import _check_origin
from .landing import router as landing_router
from .live_finance import (
    LiveBudgetDenied,
    LiveFinanceError,
    LiveFinanceUnavailable,
    LiveIdempotencyConflict,
    LiveScanExecutor,
    attach_live_scan_ids,
    finish_live_scan,
    reserve_live_scan,
    scan_fingerprint,
)
from .metrics import point_difficulty, scan_coverage, scan_metrics
from .scan import SimulatedMapsFetcher, compare, create_scans, make_fetcher, run_scan, uses_dataforseo
from .utils.qrcode import generate_qrcode_svg_data_url


# --------------------------------------------------
# Configuration
# --------------------------------------------------

STATIC = Path(__file__).parent / "static"

log = logging.getLogger("geogrid")

state: dict = {
    "fetcher": None,
}

# Scans run one at a time.
# Each scan already fetches points concurrently.
# Queued scans wait for the previous scan to finish.

scan_lock = asyncio.Lock()


# --------------------------------------------------
# Fetcher
# --------------------------------------------------

def fetcher():
    """Return the shared fetcher instance."""

    if state["fetcher"] is None:
        state["fetcher"] = make_fetcher()

    return state["fetcher"]


# --------------------------------------------------
# Application lifecycle
# --------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize database and manage application resources."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    db.init_db()
    db.mark_interrupted_scans()

    yield

    if state["fetcher"]:
        await state["fetcher"].close()


# --------------------------------------------------
# FastAPI application
# --------------------------------------------------

app = FastAPI(
    title="GeoGrid",
    lifespan=lifespan,
)

app.add_middleware(ExternalRequestSizeLimitMiddleware)
app.include_router(external_router)
app.include_router(landing_router)
app.include_router(business_finder_router)
app.include_router(commercial_router)
app.include_router(wordpress_router)


@app.middleware("http")
async def protect_landing_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(
        (
            "/landing",
            "/diagnostico",
            "/api/landing/",
            "/api/business-finder/",
            "/api/commercial/",
            "/api/wordpress/",
        )
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

app.mount(
    "/static",
    StaticFiles(directory=STATIC),
    name="static",
)


# --------------------------------------------------
# Request models
# --------------------------------------------------

class BusinessIn(BaseModel):
    """Business registration request."""

    query: str = Field(
        min_length=2,
        description="Google Maps link or 'Name, City'",
    )


class ScanIn(BaseModel):
    """GeoGrid scan configuration."""

    business_id: int

    keywords: list[str] = Field(
        min_length=1,
        max_length=10,
    )

    grid_size: int = Field(
        default=7,
        ge=3,
        le=13,
    )

    spacing_km: float = Field(
        default=1.0,
        gt=0,
        le=50,
    )

    center_lat: float | None = None
    center_lng: float | None = None


# --------------------------------------------------
# Web interface
# --------------------------------------------------

@app.get("/")
def index():
    """Serve the web interface with configured map and report branding."""

    page = (STATIC / "index.html").read_text(encoding="utf-8")
    api_key = escape(os.environ.get("GEOGRID_MAPTILER_API_KEY", "").strip(), quote=True)
    print_intro, print_closing = get_print_report_sections(
        generate_qrcode_svg_data_url(BRANDING.whatsapp_link)
    )
    page = page.replace("__GEOGRID_MAPTILER_API_KEY__", api_key)
    page = page.replace("__GEOGRID_MANUAL_SCAN_URL__", (
        "/api/commercial/admin/live-scans" if uses_dataforseo() else "/api/scans"
    ))
    page = page.replace("__ZURI_PRINT_INTRO__", print_intro)
    page = page.replace("__ZURI_PRINT_CLOSING__", print_closing)
    return HTMLResponse(page)


# --------------------------------------------------
# Businesses API
# --------------------------------------------------

@app.get("/api/businesses")
def businesses():
    """List registered businesses."""

    return db.list_businesses()


@app.post("/api/businesses")
async def add_business(body: BusinessIn):
    """Register or update a business from Google Maps."""

    try:
        info = await fetcher().resolve_business(
            body.query.strip()
        )

    except FetchError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    except Exception as error:
        log.exception("resolve failed")

        raise HTTPException(
            status_code=502,
            detail=f"lookup failed: {error}",
        ) from error

    business_id = db.upsert_business(info)

    return db.get_business(business_id)


# --------------------------------------------------
# Scans API
# --------------------------------------------------
@app.get("/api/scans")
def scans(business_id: int | None = None):
    """List scans, optionally filtered by business."""
    return db.list_scans(business_id)


async def _run_queued(
    scan_ids: list[int],
    *,
    simulated: bool = False,
) -> None:
    """Execute queued scans sequentially."""

    async with scan_lock:

        for scan_id in scan_ids:

            try:
                scan = db.get_scan(scan_id, with_points=False)
                if scan is None:
                    raise ValueError(f"scan {scan_id} does not exist")
                if scan["status"] in {"done", "failed"}:
                    log.info("scan %s already finalized; duplicate queue entry skipped", scan_id)
                    continue
                if scan["progress"] > 0:
                    db.finish_scan(
                        scan_id,
                        "failed",
                        None,
                        None,
                        "partial scan was not automatically resubmitted",
                    )
                    log.warning(
                        "scan %s has partial work; automatic resubmission was prevented",
                        scan_id,
                    )
                    continue
                scan_fetcher = (
                    SimulatedMapsFetcher(scan["business_name"])
                    if simulated
                    else fetcher()
                )
                db.set_scan_result_provider(scan_id, "simulator" if simulated else "playwright")
                await run_scan(
                    scan_id,
                    scan_fetcher,
                )
                if simulated:
                    from .prospecting import capture_completed_scan

                    try:
                        capture_completed_scan(scan_id)
                    except Exception:
                        log.exception(
                            "opportunity capture failed for completed scan %s",
                            scan_id,
                        )

            except Exception:
                log.exception(
                    "scan %s crashed",
                    scan_id,
                )


@app.post("/api/scans")
async def start_scans(
    body: ScanIn,
):
    """Create and queue GeoGrid scans."""

    if uses_dataforseo():
        raise HTTPException(
            status_code=403,
            detail=(
                "Paid DataForSEO scans are disabled on this legacy endpoint; "
                "use the authorized commercial scan flow"
            ),
        )

    business = db.get_business(
        body.business_id
    )

    if not business:
        raise HTTPException(
            status_code=404,
            detail="business not found",
        )

    center = (
        (body.center_lat, body.center_lng)
        if (
            body.center_lat is not None
            and body.center_lng is not None
        )
        else None
    )

    try:
        scan_ids = create_scans(
            business,
            body.keywords,
            body.grid_size,
            body.spacing_km,
            center,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    asyncio.create_task(
        _run_queued(scan_ids)
    )

    return {
        "scan_ids": scan_ids,
    }


async def _run_live_batch(executor: LiveScanExecutor) -> None:
    try:
        provider = fetcher()
        for index, scan_id in enumerate(executor.scan_ids):
            scan = db.get_scan(scan_id, with_points=False)
            if not scan or scan["status"] != "running" or scan["progress"]:
                raise RuntimeError("Live scan cannot be safely started")
            await run_scan(
                scan_id,
                provider,
                concurrency=executor.max_in_flight,
                paid_fetch=partial(
                    executor.fetch_point,
                    provider,
                    scan_index=index,
                    scan_id=scan_id,
                    grid_size=scan["grid_size"],
                ),
            )
            if db.get_scan(scan_id, with_points=False)["status"] == "failed":
                break
    finally:
        await executor.finish()
        for scan_id in executor.scan_ids:
            scan = db.get_scan(scan_id, with_points=False)
            if scan and scan["status"] == "running":
                db.finish_scan(scan_id, "failed", None, None, "Live batch interrupted")


@app.post("/api/commercial/admin/live-scans", status_code=202)
def start_live_scans(
    body: ScanIn,
    request: Request,
    background: BackgroundTasks,
    idempotency_key: UUID = Header(),
):
    _check_origin(request)
    _require_secure_admin_request(request)
    operator = _require_admin_session(request)
    if (
        not uses_dataforseo()
        or os.environ.get("GEOGRID_MANUAL_LIVE_ENABLED") != "true"
        or not landing_diagnostics_gate()["enabled"]
    ):
        raise HTTPException(status_code=503, detail="Manual Live is disabled")
    if not all(os.environ.get(key, "").strip() for key in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD")):
        raise HTTPException(status_code=503, detail="Live provider credentials are unavailable")

    business = db.get_business(body.business_id)
    if not business:
        raise HTTPException(status_code=404, detail="business not found")
    keywords = [keyword.strip() for keyword in body.keywords if keyword.strip()]
    lat = business["lat"] if body.center_lat is None else body.center_lat
    lng = business["lng"] if body.center_lng is None else body.center_lng
    if (
        not keywords
        or len(set(keywords)) != len(keywords)
        or any(len(keyword) > 255 for keyword in keywords)
        or not (-90 < lat < 90 and -180 <= lng <= 180)
        or (body.center_lat is None) != (body.center_lng is None)
    ):
        raise HTTPException(status_code=422, detail="Invalid Live scan parameters")
    try:
        validate_grid(body.grid_size, body.spacing_km)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    fingerprint = scan_fingerprint(
        operator_id=operator,
        business_id=body.business_id,
        keywords=keywords,
        grid_size=body.grid_size,
        spacing_km=body.spacing_km,
        center=(lat, lng),
    )
    try:
        reservation = reserve_live_scan(
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            operator_id=operator,
            business_id=body.business_id,
            grid_size=body.grid_size,
            keyword_count=len(keywords),
        )
    except (LiveBudgetDenied, LiveIdempotencyConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LiveFinanceError as error:
        raise HTTPException(status_code=503, detail="Live finance unavailable") from error
    if not reservation.created:
        return {"scan_ids": reservation.scan_ids or [], "status": reservation.status}

    scan_ids = []
    try:
        for keyword in keywords:
            scan_id = db.create_scan(body.business_id, keyword, body.grid_size, body.spacing_km, lat, lng)
            scan_ids.append(scan_id)
            db.set_scan_result_provider(scan_id, "dataforseo_live")
        if not attach_live_scan_ids(idempotency_key, reservation.claim_token, scan_ids):
            raise LiveFinanceUnavailable("Unable to attach reserved scan IDs")
        background.add_task(
            _run_live_batch,
            LiveScanExecutor(idempotency_key, reservation.claim_token, operator, scan_ids),
        )
    except Exception as error:
        log.exception("Live scan could not be queued")
        try:
            finish_live_scan(idempotency_key)
        except LiveFinanceError:
            log.exception("Live reservation remains held")
        for scan_id in scan_ids:
            db.finish_scan(scan_id, "failed", None, None, "Live scan could not be queued")
        raise HTTPException(status_code=503, detail="Live scan could not be queued") from error

    return {"scan_ids": scan_ids, "status": "queued"}


# --------------------------------------------------
# Scan details
# --------------------------------------------------

@app.get("/api/scans/{scan_id}")
def scan_detail(
    scan_id: int,
):
    """Return scan details and historical comparison."""

    scan = db.get_scan(scan_id)

    if not scan:
        raise HTTPException(
            status_code=404,
            detail="scan not found",
        )

    scan["comparison"] = None

    if scan["status"] == "done":

        previous_id = db.previous_scan_id(scan)

        if previous_id:

            previous_scan = db.get_scan(
                previous_id
            )

            scan["comparison"] = compare(
                scan,
                previous_scan,
            )

    return scan


# --------------------------------------------------
# Delete scan
# --------------------------------------------------

@app.delete("/api/scans/{scan_id}")
def remove_scan(
    scan_id: int,
):
    """Delete a scan that is not currently running."""

    scan = db.get_scan(
        scan_id,
        with_points=False,
    )

    if not scan:
        raise HTTPException(
            status_code=404,
            detail="scan not found",
        )

    if scan["status"] in {"running", "queued", "processing"} or (
        scan["status"] == "partial" and not scan.get("finished")
    ):
        raise HTTPException(
            status_code=409,
            detail="scan is still running",
        )

    db.delete_scan(scan_id)

    return {
        "deleted": scan_id,
    }


# --------------------------------------------------
# GeoGrid ZURI - scan analysis
# --------------------------------------------------

@app.get("/api/scans/{scan_id}/analysis")
def scan_analysis(
    scan_id: int,
    category: list[str] | None = Query(default=None),
    related_category: list[str] | None = Query(default=None),
):
    """Return local visibility and experimental competitive indicators.

    Metrics:
        ARP: Average Rank Position.
        ATRP: Average Total Rank Position.
        SoLV: Share of Local Voice.
        IVL: Local Visibility Index.
        ICM, ICP-B V2, ICE, IOP and IDA: experimental/descriptive indicators.

    Failed scan points are excluded from ranking metrics.
    """

    # ----------------------------------------------
    # 1. Load scan
    # ----------------------------------------------

    scan = db.get_scan(scan_id)

    if not scan:
        raise HTTPException(
            status_code=404,
            detail="scan not found",
        )

    scan_points = scan.get("points") or []
    target = {
        "business_cid": scan.get("business_cid"),
        "business_place_id": scan.get("business_place_id"),
    }
    target_category = scan.get("business_category")
    category_terms = category or (
        [target_category] if target_category else []
    )

    # ----------------------------------------------
    # 2. Filter successfully scanned points
    # ----------------------------------------------

    valid = [
        point
        for point in scan_points
        if point.get("status") == "ok"
    ]

    # ----------------------------------------------
    # 3. Calculate visibility metrics
    # ----------------------------------------------

    ranks = [
        point.get("rank")
        for point in valid
    ]

    metrics = scan_metrics(ranks)

    coverage = scan_coverage(scan_points, scan.get("total", len(scan_points)))
    metrics.update(coverage)
    metrics["errors"] = coverage["failed_points"]
    valid_count = coverage["valid_points"]

    # ----------------------------------------------
    # 4. Calculate difficulty for each point
    # ----------------------------------------------

    points = []

    for point in scan_points:

        if point.get("status") == "ok":

            difficulty = point_difficulty(
                point.get("results") or [],
                category_terms,
                target=target,
            )

        elif point.get("status") == "no_results":
            difficulty = {
                "ida": None,
                "level": "dados_insuficientes",
                "confidence": "not_estimated",
                "evidence": "insufficient",
                "reason": "sem_resultados",
            }
        elif point.get("status") == "pending":
            difficulty = {
                "ida": None,
                "level": "dados_insuficientes",
                "confidence": "not_estimated",
                "evidence": "insufficient",
                "reason": "aguardando_processamento",
            }
        else:
            difficulty = {
                "ida": None,
                "level": "dados_insuficientes",
                "confidence": "not_estimated",
                "evidence": "insufficient",
                "reason": "erro_na_consulta",
            }

        points.append(
            {
                "row": point.get("row"),
                "col": point.get("col"),
                "lat": point.get("lat"),
                "lng": point.get("lng"),
                "rank": point.get("rank"),
                "status": point.get("status"),
                "error": point.get("error"),
                "error_category": point.get("error_category"),
                "api_status_code": point.get("api_status_code"),
                "api_status_message": point.get("api_status_message"),
                "difficulty": difficulty,
            }
        )

    # ----------------------------------------------
    # 5. Collect valid difficulty scores
    # ----------------------------------------------

    scores = [
        point["difficulty"]["ida"]
        for point in points
        if point["difficulty"].get("ida") is not None
    ]

    # ----------------------------------------------
    # 6. Calculate average difficulty
    # ----------------------------------------------

    ida_average = (
        round(
            sum(scores) / len(scores),
            1,
        )
        if scores
        else None
    )

    # ----------------------------------------------
    # 7. Calculate difficulty data coverage
    # ----------------------------------------------

    ida_coverage_pct = (
        round(
            100 * len(scores) / valid_count,
            1,
        )
        if valid_count
        else 0.0
    )

    # ----------------------------------------------
    # 8. Calculate observed brand and competition indicators
    # ----------------------------------------------

    icm = target_brand_confidence(scan_points, target)
    icpb_v2 = keyword_competition_v2(
        scan_points,
        target=target,
    )
    ice = classify_competitors(
        scan_points,
        target=target,
        target_category=target_category,
        keyword=scan.get("keyword"),
        related_categories=related_category,
    )
    iop = position_opportunity(scan_points, icpb_v2)

    # ----------------------------------------------
    # 9. Generate interpretations
    # ----------------------------------------------

    analysis_result = {
        "scan_id": scan_id,
        "keyword": scan.get("keyword"),
        "target_category": target_category,
        "metrics": metrics,
        "icm": icm,
        "icpb_v2": icpb_v2,
        "ice": ice,
        "iop": iop,
        "ida_average": ida_average,
        "ida_evaluated_points": len(scores),
        "ida_coverage_pct": ida_coverage_pct,
        "points": points,
    }

    interpretation = generate_full_interpretation(analysis_result, scan.get("keyword", ""))
    analysis_result["interpretation"] = interpretation

    # ----------------------------------------------
    # 10. Return complete analysis
    # ----------------------------------------------

    return analysis_result
