"""Public, capability-protected Landing Page integration endpoints."""

import asyncio
import math
import os
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool


router = APIRouter(tags=["landing"])
STATIC = Path(__file__).parent / "static"
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_rate_lock = asyncio.Lock()
_rate_entries: dict[str, deque[float]] = defaultdict(deque)


class LeadSubmission(BaseModel):
    request_id: UUID
    status_token: str = Field(min_length=64, max_length=64)
    contact_name: str = Field(min_length=2, max_length=200)
    company_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=254)
    whatsapp: str = Field(min_length=8, max_length=32)
    city: str = Field(min_length=2, max_length=120)
    state: str = Field(min_length=2, max_length=80)
    google_maps_url: str | None = Field(default=None, max_length=2000)
    keyword: str = Field(min_length=2, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=128)
    privacy_acknowledged: StrictBool
    marketing_consent: StrictBool = False
    website: str = Field(default="", max_length=200)


class StatusLookup(BaseModel):
    status_token: str = Field(min_length=64, max_length=64)


def _configured_url(name: str, request_id: UUID | None = None) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail="Landing integration is not configured")
    parsed = urlparse(value)
    allowed_internal_hosts = {
        host.strip().lower()
        for host in os.environ.get("ZURI_N8N_INTERNAL_HTTP_HOSTS", "").split(",")
        if host.strip()
    }
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    internal_http = (
        parsed.scheme == "http"
        and parsed.hostname is not None
        and parsed.hostname.lower() in allowed_internal_hosts
    )
    if parsed.scheme != "https" and not local_http and not internal_http:
        raise HTTPException(status_code=503, detail="Landing integration URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(status_code=503, detail="Invalid landing integration URL")
    if request_id:
        value = value.replace("{request_id}", str(request_id))
    return value


def _campaign_ids() -> set[str]:
    configured = os.environ.get("ZURI_ALLOWED_CAMPAIGNS", "")
    campaigns = {item.strip() for item in configured.split(",") if item.strip()}
    default = os.environ.get("ZURI_CAMPAIGN_ID", "").strip()
    if default:
        campaigns.add(default)
    return campaigns


def _configured_policy() -> tuple[str, str]:
    policy_url = os.environ.get("ZURI_PRIVACY_POLICY_URL", "").strip()
    parsed = urlparse(policy_url)
    policy_version = os.environ.get("ZURI_PRIVACY_POLICY_VERSION", "").strip()
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not policy_version
    ):
        raise HTTPException(status_code=503, detail="Privacy policy is not configured")
    return policy_url, policy_version


def _origin_key(value: str) -> tuple[str, str] | None:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.scheme.lower(), parsed.netloc.lower()


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    configured_values = [
        item.strip()
        for item in os.environ.get("ZURI_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    ]
    if configured_values:
        origins = [_origin_key(item) for item in configured_values]
        if any(item is None for item in origins):
            raise HTTPException(status_code=503, detail="Invalid allowed-origin configuration")
        configured = set(origins)
    else:
        default_origin = _origin_key(str(request.base_url))
        configured = {default_origin} if default_origin else set()
    if _origin_key(origin) not in configured:
        raise HTTPException(status_code=403, detail="Cross-origin request is not allowed")


async def _check_rate_limit(request: Request, bucket: str) -> None:
    client = request.client.host if request.client else "unknown"
    key = f"{bucket}:{client}"
    now = time.monotonic()
    window = max(60, int(os.environ.get("ZURI_LEADS_RATE_WINDOW_SECONDS", "900")))
    defaults = {"leads": 600, "status": 120, "report": 20}
    limit = max(1, int(os.environ.get(f"ZURI_{bucket.upper()}_RATE_LIMIT", str(defaults[bucket]))))
    async with _rate_lock:
        history = _rate_entries[key]
        while history and history[0] <= now - window:
            history.popleft()
        if len(history) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests; try again later")
        history.append(now)


def _normalize_lead(lead: LeadSubmission) -> dict:
    if not _TOKEN.fullmatch(lead.status_token):
        raise HTTPException(status_code=422, detail="Invalid status capability")
    if not lead.privacy_acknowledged:
        raise HTTPException(status_code=422, detail="Privacy notice acknowledgement is required")
    email = lead.email.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(status_code=422, detail="Invalid email")
    phone = re.sub(r"[^\d+]", "", lead.whatsapp)
    if not re.fullmatch(r"\+?\d{8,20}", phone):
        raise HTTPException(status_code=422, detail="Invalid WhatsApp number")
    if lead.request_id.version != 4:
        raise HTTPException(status_code=422, detail="Invalid request ID")
    if lead.campaign_id not in _campaign_ids():
        raise HTTPException(status_code=422, detail="Invalid campaign")
    _policy_url, policy_version = _configured_policy()
    contact_name = lead.contact_name.strip()
    company_name = lead.company_name.strip()
    city = lead.city.strip()
    state = lead.state.strip().upper()
    keyword = lead.keyword.strip()
    if any(len(value) < 2 for value in (contact_name, company_name, city, state, keyword)):
        raise HTTPException(status_code=422, detail="Name, company, location, and keyword are required")
    maps_url = lead.google_maps_url.strip() if lead.google_maps_url else ""
    if maps_url:
        parsed = urlparse(maps_url)
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "google.com"
            or host.endswith(".google.com")
            or host in {"maps.app.goo.gl", "goo.gl"}
        )
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
            raise HTTPException(status_code=422, detail="Invalid Google Maps URL")
        maps_url = parsed.geturl()
    return {
        "request_id": str(lead.request_id),
        "status_token": lead.status_token,
        "contact_name": contact_name,
        "company_name": company_name,
        "email": email,
        "whatsapp": phone,
        "city": city,
        "state": state,
        "google_maps_url": maps_url or None,
        "keyword": keyword,
        "campaign_id": lead.campaign_id,
        "privacy_acknowledged": True,
        "privacy_policy_version": policy_version,
        "marketing_consent": lead.marketing_consent,
        "website": lead.website,
    }


async def _post_to_n8n(url: str, payload: dict) -> dict:
    try:
        timeout_seconds = float(os.environ.get("ZURI_N8N_TIMEOUT_SECONDS", "30"))
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Landing integration timeout is misconfigured",
        ) from error
    if not 1 <= timeout_seconds <= 120:
        raise HTTPException(
            status_code=503,
            detail="Landing integration timeout is misconfigured",
        )

    try:
        max_attempts = int(os.environ.get("ZURI_N8N_RETRY_ATTEMPTS", "3"))
        base_delay = float(os.environ.get("ZURI_N8N_RETRY_BASE_SECONDS", "0.25"))
        max_delay = float(os.environ.get("ZURI_N8N_RETRY_MAX_DELAY_SECONDS", "10"))
    except ValueError as error:
        raise HTTPException(status_code=503, detail="Landing retry policy is misconfigured") from error
    if not 1 <= max_attempts <= 5 or not 0 <= base_delay <= 10 or not 0 <= max_delay <= 60:
        raise HTTPException(status_code=503, detail="Landing retry policy is misconfigured")

    def retry_after_seconds(value: str | None) -> int | None:
        if not value:
            return None
        if value.strip().isdigit():
            return max(0, int(value.strip()))
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((date - datetime.now(timezone.utc)).total_seconds()))

    last_error: httpx.TransportError | None = None
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout_seconds,
            connect=min(timeout_seconds, 5),
            pool=min(timeout_seconds, 5),
        ),
        follow_redirects=False,
    ) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.post(url, json=payload)
                last_error = None
            except httpx.TransportError as error:
                last_error = error
                response = None

            retry_after = retry_after_seconds(
                response.headers.get("Retry-After") if response is not None else None
            )
            retryable_status = response is not None and (
                response.status_code == 429 or response.status_code >= 500
            )
            if attempt + 1 < max_attempts and (last_error is not None or retryable_status):
                exponential = min(max_delay, base_delay * (2 ** attempt))
                delay = (
                    retry_after
                    if retry_after is not None
                    else random.uniform(exponential * 0.5, exponential * 1.5)
                )
                if delay <= 60:
                    await asyncio.sleep(delay)
                    continue
            if last_error is not None:
                detail = (
                    "Integration service timed out; retry the same request"
                    if isinstance(last_error, httpx.TimeoutException)
                    else "Integration service is temporarily unavailable; retry the same request"
                )
                raise HTTPException(status_code=503, detail=detail) from last_error
            assert response is not None
            if response.status_code not in (200, 202):
                headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
                raise HTTPException(
                    status_code=503 if response.status_code >= 500 else response.status_code,
                    detail="Lead service could not accept this request",
                    headers=headers,
                )
            break
    assert response is not None
    try:
        result = response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="Invalid response from lead service") from error
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Invalid response from lead service")
    return result


def _page(name: str) -> HTMLResponse:
    page = (STATIC / name).read_text(encoding="utf-8")
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/landing", response_class=HTMLResponse)
def landing_page():
    return _page("landing.html")


@router.get("/diagnostico", response_class=HTMLResponse)
def diagnostic_page():
    return _page("diagnostic.html")


@router.get("/api/landing/config")
def landing_config(request: Request):
    policy_url = os.environ.get("ZURI_PRIVACY_POLICY_URL", "").strip()
    parsed_policy = urlparse(policy_url)
    if policy_url and (
        parsed_policy.scheme != "https"
        or not parsed_policy.hostname
        or parsed_policy.username
        or parsed_policy.password
        or parsed_policy.query
        or parsed_policy.fragment
    ):
        policy_url = ""
    policy_version = os.environ.get("ZURI_PRIVACY_POLICY_VERSION", "").strip()
    campaign_id = request.query_params.get("campaign", "").strip()
    campaigns = _campaign_ids()
    if campaign_id not in campaigns:
        campaign_id = os.environ.get("ZURI_CAMPAIGN_ID", "").strip()
    return {
        "enabled": bool(
            os.environ.get("ZURI_N8N_LEADS_WEBHOOK_URL", "").strip()
            and bool(campaign_id)
            and policy_url
            and policy_version
        ),
        "campaign_id": campaign_id,
        "privacy_policy_url": policy_url,
        "privacy_policy_version": policy_version,
    }


@router.post("/api/landing/leads")
async def submit_lead(lead: LeadSubmission, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "leads")
    payload = _normalize_lead(lead)
    if payload["website"]:
        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "request_id": payload["request_id"],
                "status": "queued",
                "duplicate": False,
                "status_token": payload["status_token"],
            },
        )
    upstream = await _post_to_n8n(
        _configured_url("ZURI_N8N_LEADS_WEBHOOK_URL"),
        payload,
    )
    if upstream.get("accepted") is not True:
        raise HTTPException(status_code=502, detail="Lead service returned an invalid receipt")
    try:
        saved_id = str(UUID(str(upstream["request_id"])))
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail="Lead service returned an invalid receipt") from error
    duplicate = bool(upstream.get("duplicate", False))
    if duplicate and saved_id != payload["request_id"]:
        raise HTTPException(status_code=409, detail="A matching request already exists")
    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "request_id": saved_id,
            "status": str(upstream.get("status", "queued")),
            "duplicate": duplicate,
            "status_token": payload["status_token"],
        },
    )


@router.post("/api/landing/status/{request_id}")
async def diagnostic_status(request_id: UUID, body: StatusLookup, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "status")
    if not _TOKEN.fullmatch(body.status_token):
        raise HTTPException(status_code=422, detail="Invalid status capability")
    url = _configured_url("ZURI_N8N_STATUS_WEBHOOK_URL", request_id)
    result = await _post_to_n8n(
        url,
        {"request_id": str(request_id), "status_token": body.status_token},
    )
    return result


@router.post("/api/landing/report/{request_id}")
async def diagnostic_report(request_id: UUID, body: StatusLookup, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "report")
    if not _TOKEN.fullmatch(body.status_token):
        raise HTTPException(status_code=422, detail="Invalid status capability")
    url = _configured_url("ZURI_N8N_REPORT_WEBHOOK_URL", request_id)
    result = await _post_to_n8n(
        url,
        {"request_id": str(request_id), "status_token": body.status_token},
    )
    return result
