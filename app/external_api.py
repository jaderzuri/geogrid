"""Authenticated, tenant-scoped API for n8n scan result ingestion."""

import hmac
import json
import os
import re
from collections import deque
from typing import Literal, NoReturn
from uuid import UUID

import psycopg

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import db

router = APIRouter(prefix="/api/external", tags=["external"])
_bearer = HTTPBearer(auto_error=False)
_MAX_REQUEST_BYTES = 1_048_576
_CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,100}\Z")


class ExternalRequestSizeLimitMiddleware:
    """Bound external request bodies, including chunked requests without a length."""

    def __init__(self, app, max_bytes: int = _MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(
            ("/api/external/", "/api/landing/", "/api/commercial/", "/api/wordpress/")
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                await self._reject(send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        replay = deque(messages)

        async def receive_buffered():
            if replay:
                return replay.popleft()
            return {"type": "http.disconnect"}

        await self.app(scope, receive_buffered, send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"detail": "Request body too large"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


if hasattr(BaseModel, "model_fields"):
    from pydantic import ConfigDict

    class ExternalInput(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
else:

    class ExternalInput(BaseModel):
        class Config:
            extra = "forbid"
            anystr_strip_whitespace = True


class ExternalBusinessIn(ExternalInput):
    external_business_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=2, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    cid: str | None = Field(default=None, max_length=128)
    place_id: str | None = Field(default=None, max_length=256)
    address: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=200)


class ExternalScanIn(ExternalInput):
    external_request_id: str = Field(min_length=1, max_length=128)
    external_business_id: str = Field(min_length=1, max_length=128)
    keyword: str = Field(min_length=2, max_length=200)


    manual_claim_token: UUID | None = None


class ExternalResultIn(ExternalInput):
    name: str = Field(min_length=1, max_length=200)
    cid: str | None = Field(default=None, max_length=128)
    place_id: str | None = Field(default=None, max_length=256)
    phone: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2000)
    position: int | None = Field(default=None, ge=1)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)


class ExternalPointIn(ExternalInput):
    row: int = Field(ge=0, le=12)
    col: int = Field(ge=0, le=12)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    status: Literal["ok", "no_results", "error", "pending"]
    dataforseo_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    results: list[ExternalResultIn] = Field(default_factory=list)
    error: str | None = Field(default=None, max_length=1000)
    error_category: Literal[
        "authentication_error",
        "access_denied",
        "insufficient_balance",
        "rate_limit",
        "duplicate_task_limit",
        "budget_limit",
        "request_error",
        "request_notice",
        "technical_error",
        "partial_results",
        "task_pending",
        "task_unavailable",
        "no_results",
    ] | None = None
    api_status_code: int | None = Field(default=None, ge=0, le=999999)
    api_status_message: str | None = Field(default=None, max_length=1000)


class ExternalPointsIn(ExternalInput):
    points: list[ExternalPointIn]


class ExternalCompleteIn(ExternalInput):
    allow_partial: bool = False
    completion_reason: str | None = Field(default=None, min_length=1, max_length=500)
    failure_reason: str | None = Field(default=None, min_length=1, max_length=500)


def require_external_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    configured = os.environ.get("GEOGRID_EXTERNAL_API_TOKEN", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="External API is disabled")
    supplied = credentials.credentials if credentials else ""
    if (
        not credentials
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(
            supplied.encode("utf-8"),
            configured.encode("utf-8"),
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _client_id(value: str) -> str:
    if not _CLIENT_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="Invalid X-GeoGrid-Client-ID")
    return value


def _dump_model(model: BaseModel, **kwargs) -> dict:
    dump = getattr(model, "model_dump", None)
    if dump:
        return dump(**kwargs)
    legacy_dump = getattr(model, "dict")
    return legacy_dump(**kwargs)


def _raise_external_error(error: Exception) -> NoReturn:
    if isinstance(error, db.ExternalScanNotFoundError):
        raise HTTPException(status_code=404, detail="External resource not found") from error
    if isinstance(error, db.ExternalPendingPointsError):
        raise HTTPException(
            status_code=409,
            detail={"message": "Scan has pending points", "pending_points": error.pending_points},
        ) from error
    if isinstance(error, db.ExternalConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise error


def _public_scan(scan: dict) -> dict:
    from .metrics import scan_coverage

    coverage = scan_coverage(scan["points"], scan["total"])
    return {
        "scan_id": scan["external_id"],
        "external_request_id": scan["external_request_id"],
        "business_id": scan["external_business_id"],
        "keyword": scan["keyword"],
        "language_code": scan["language_code"],
        "status": scan["external_status"],
        "grid_size": scan["grid_size"],
        "spacing_km": scan["spacing_km"],
        "center": {"lat": scan["center_lat"], "lng": scan["center_lng"]},
        "progress": scan["progress"],
        "total": scan["total"],
        "created": scan["external_created"],
        "updated": scan["external_updated"],
        "finished": scan["external_finished"],
        "completion_reason": scan["completion_reason"],
        "metrics": scan["metrics"],
        "coverage": coverage,
        "competitors": scan["competitors"],
        "points": [
            {
                "row": point["row"],
                "col": point["col"],
                "lat": point["lat"],
                "lng": point["lng"],
                "rank": point["rank"],
                "status": point["status"],
                "dataforseo_task_id": point["external_task_id"],
                "error": point["error"],
                "error_category": point["error_category"],
                "api_status_code": point["api_status_code"],
                "api_status_message": point["api_status_message"],
                "results": point["results"],
            }
            for point in scan["points"]
        ],
    }


def _as_point_payload(point: ExternalPointIn) -> dict:
    if len(point.results) > 20:
        raise HTTPException(status_code=422, detail="A point may contain at most 20 results")
    if point.status == "ok" and not point.results:
        raise HTTPException(status_code=422, detail="An ok point must contain results")
    if point.status in {"no_results", "pending"} and point.results:
        raise HTTPException(
            status_code=422,
            detail=f"A {point.status} point cannot contain results",
        )
    if point.status == "error" and not (point.error or point.error_category):
        raise HTTPException(
            status_code=422,
            detail="An error point must include error or error_category",
        )
    return {
        "row": point.row,
        "col": point.col,
        "lat": point.lat,
        "lng": point.lng,
        "status": point.status,
        "task_id": point.dataforseo_task_id,
        "results": [_dump_model(result) for result in point.results],
        "error": point.error,
        "error_category": point.error_category,
        "api_status_code": point.api_status_code,
        "api_status_message": point.api_status_message,
    }


@router.post("/businesses", dependencies=[Depends(require_external_auth)])
def register_business(
    body: ExternalBusinessIn,
    client_id: str = Header(alias="X-GeoGrid-Client-ID"),
):
    """Register or idempotently identify a business within one external client."""
    client_id = _client_id(client_id)
    try:
        business, created = db.register_external_business(
            client_id,
            body.external_business_id,
            _dump_model(body, exclude={"external_business_id"}),
        )
    except (db.ExternalConflictError, db.ExternalScanNotFoundError) as error:
        _raise_external_error(error)
    return {**business, "created": created}


@router.post("/scans", dependencies=[Depends(require_external_auth)])
def register_scan(
    body: ExternalScanIn,
    client_id: str = Header(alias="X-GeoGrid-Client-ID"),
):
    """Create one fixed 3x3 / 1 km scan without calling any search provider."""
    client_id = _client_id(client_id)
    language_code = os.environ.get("GEOGRID_EXTERNAL_LANGUAGE_CODE", "pt").strip() or "pt"
    options = {}
    if body.manual_claim_token is not None:
        from .manual_standard import read_worker_claim
        from .live_finance import LiveFinanceError

        try:
            request_id = UUID(body.external_request_id)
            claim = read_worker_claim(request_id, body.manual_claim_token)
        except (ValueError, LiveFinanceError, psycopg.Error) as error:
            raise HTTPException(503, "Manual queue claim unavailable") from error
        if (
            client_id != "t2s-landing" or not claim or claim["keyword"] != body.keyword
            or body.external_business_id != f"manual-{claim['manual_business_id']}"
        ):
            raise HTTPException(403, "Invalid manual queue claim")
        options = dict(registered_business_id=claim["manual_business_id"],
                       grid_size=claim["grid_size"], spacing_km=claim["spacing_km"])
        language_code = "pt"
    try:
        scan, created = db.create_external_scan(
            client_id,
            body.external_request_id,
            body.external_business_id,
            body.keyword,
            language_code,
            **options,
        )
    except (db.ExternalConflictError, db.ExternalScanNotFoundError) as error:
        _raise_external_error(error)
    except db.ExternalInvalidGridError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    response = _public_scan(scan)
    response["created_new"] = created
    return response


@router.get("/scans/{scan_id}", dependencies=[Depends(require_external_auth)])
def read_scan(
    scan_id: str,
    client_id: str = Header(alias="X-GeoGrid-Client-ID"),
):
    """Return a client-scoped scan and the shared indicator analysis."""
    client_id = _client_id(client_id)
    try:
        scan = db.get_external_scan(client_id, scan_id)
    except db.ExternalScanNotFoundError as error:
        _raise_external_error(error)

    from .main import scan_analysis

    analysis = scan_analysis(scan["id"], None, None)
    analysis["scan_id"] = scan_id
    response = _public_scan(scan)
    response["business"] = {
        "name": scan["business_name"],
        "cid": scan["business_cid"],
        "place_id": scan["business_place_id"],
        "category": scan["business_category"],
    }
    response["analysis"] = analysis
    return response


@router.get("/scans/{scan_id}/pdf", dependencies=[Depends(require_external_auth)])
async def basic_scan_pdf(scan_id: UUID, client_id: str = Header(alias="X-GeoGrid-Client-ID")):
    from .server_reports import basic_pdf

    try:
        scan = db.get_external_scan(_client_id(client_id), str(scan_id))
    except db.ExternalScanNotFoundError as error:
        _raise_external_error(error)
    return await basic_pdf([scan])


@router.post("/scans/{scan_id}/points", dependencies=[Depends(require_external_auth)])
def receive_points(
    scan_id: str,
    body: ExternalPointsIn,
    client_id: str = Header(alias="X-GeoGrid-Client-ID"),
):
    """Idempotently ingest up to 169 per-point DataForSEO outcomes."""
    client_id = _client_id(client_id)
    if not body.points or len(body.points) > 169:
        raise HTTPException(status_code=422, detail="Submit between one and 169 points")
    cells = [(point.row, point.col) for point in body.points]
    if len(cells) != len(set(cells)):
        raise HTTPException(status_code=422, detail="A point may appear only once per request")
    try:
        saved = db.save_external_points(
            client_id,
            scan_id,
            [_as_point_payload(point) for point in body.points],
        )
    except (db.ExternalConflictError, db.ExternalScanNotFoundError) as error:
        _raise_external_error(error)
    scan = saved["scan"]
    return {
        "scan_id": scan_id,
        "status": scan["external_status"],
        "progress": scan["progress"],
        "total": scan["total"],
        "accepted_points": saved["accepted"],
        "idempotent": saved["idempotent"],
    }


@router.post("/scans/{scan_id}/complete", dependencies=[Depends(require_external_auth)])
def finish_scan(
    scan_id: str,
    body: ExternalCompleteIn,
    client_id: str = Header(alias="X-GeoGrid-Client-ID"),
):
    """Finalize only after all points resolve, unless a reasoned partial close is requested."""
    client_id = _client_id(client_id)
    if body.allow_partial and not body.completion_reason:
        raise HTTPException(
            status_code=422,
            detail="completion_reason is required when allow_partial is true",
        )
    if body.allow_partial and body.failure_reason:
        raise HTTPException(
            status_code=422,
            detail="allow_partial and failure_reason cannot be combined",
        )
    try:
        result = db.complete_external_scan(
            client_id,
            scan_id,
            allow_partial=body.allow_partial,
            completion_reason=body.completion_reason,
            failure_reason=body.failure_reason,
        )
    except (
        db.ExternalConflictError,
        db.ExternalPendingPointsError,
        db.ExternalScanNotFoundError,
    ) as error:
        _raise_external_error(error)
    opportunity_capture = None
    if result["scan"]["status"] == "done":
        from .prospecting import capture_completed_scan

        opportunity_capture = capture_completed_scan(int(result["scan"]["id"]))
    return {
        "scan_id": scan_id,
        "status": result["scan"]["external_status"],
        "progress": result["scan"]["progress"],
        "total": result["scan"]["total"],
        "finished": result["scan"]["external_finished"],
        "completion_reason": result["scan"]["completion_reason"],
        "idempotent": result["idempotent"],
        "opportunity_capture": opportunity_capture,
    }


@router.get("/health", dependencies=[Depends(require_external_auth)])
def external_health():
    if not db.external_database_health():
        raise HTTPException(status_code=503, detail="External API storage is unavailable")
    return {"status": "ok", "storage": "ok", "dataforseo_calls_enabled": False}
