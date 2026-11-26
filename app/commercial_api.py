"""Simulated commercial journey and authenticated WordPress server API."""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, StrictBool

from . import business_finder, commercial_store, db, main, prospecting
from .business_finder import (
    BusinessConfirmIn,
    BusinessDiagnosticIn,
    BusinessSearchIn,
    _decode_token,
    _query_from_input,
    _query_hash,
    _signing_secret,
    confirm_business,
    search_business,
    start_business_diagnostic,
)
from .commercial import (
    commercial_limits,
    commercial_product_catalog,
    landing_diagnostics_gate,
    prospecting_storage_approved,
    real_express_gate,
    simulator_ready,
)
from .landing import (
    LeadSubmission,
    _campaign_ids,
    _check_origin,
    _check_rate_limit,
    _configured_policy,
    submit_lead as submit_landing_lead,
)
from .metrics import scan_coverage

router = APIRouter(tags=["commercial"])
wordpress_router = APIRouter(prefix="/api/wordpress/v1", tags=["wordpress"])

_TOKEN = re.compile(r"[a-f0-9]{64}\Z")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE = re.compile(r"\+?\d{8,20}\Z")
_OPERATOR_ID = re.compile(r"[A-Za-z0-9_.@-]{2,100}\Z")


if hasattr(BaseModel, "model_fields"):
    from pydantic import ConfigDict

    class CommerceInput(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
else:

    class CommerceInput(BaseModel):
        class Config:
            extra = "forbid"
            anystr_strip_whitespace = True


class CommercialLeadIn(CommerceInput):
    request_id: uuid.UUID
    status_token: str = Field(min_length=64, max_length=64)
    business_confirmed: StrictBool
    confirmation_token: str | None = Field(default=None, min_length=30, max_length=4096)
    search_request_id: uuid.UUID | None = None
    business_search: BusinessSearchIn | None = None
    business_name: str = Field(min_length=2, max_length=200)
    city: str = Field(min_length=2, max_length=120)
    state: str = Field(min_length=2, max_length=80)
    address: str | None = Field(default=None, min_length=2, max_length=300)
    google_maps_url: str | None = Field(default=None, max_length=2000)
    contact_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=254)
    whatsapp: str = Field(min_length=8, max_length=32)
    campaign_id: str = Field(min_length=1, max_length=128)
    privacy_acknowledged: StrictBool
    marketing_consent: StrictBool = False
    website: str = Field(default="", max_length=200)


class CommercialDiagnosticIn(CommerceInput):
    request_id: uuid.UUID
    lead_id: uuid.UUID
    status_token: str = Field(min_length=64, max_length=64)
    product_id: Literal["express"]
    confirmation_token: str = Field(min_length=30, max_length=4096)
    search: BusinessSearchIn
    keyword: str = Field(min_length=2, max_length=200)
    confirmed: StrictBool


class CommercialCapabilityIn(CommerceInput):
    lead_id: uuid.UUID
    status_token: str = Field(min_length=64, max_length=64)


class CommercialOrderIn(CommerceInput):
    request_id: uuid.UUID
    lead_id: uuid.UUID
    status_token: str = Field(min_length=64, max_length=64)
    source_diagnostic_id: uuid.UUID
    product_id: Literal["professional", "monitor"]


class OperatorPaymentIn(CommerceInput):
    payment_reference: str = Field(min_length=3, max_length=128)


class AdminLoginIn(CommerceInput):
    operator_id: str = Field(min_length=2, max_length=100)
    operator_token: str = Field(min_length=32, max_length=512)


class WordPressConfirmationIn(CommerceInput):
    request_id: uuid.UUID
    candidate_token: str = Field(min_length=30, max_length=4096)
    confirmed: StrictBool


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_simulator() -> None:
    if (
        not simulator_ready()
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "category": "SIMULATOR_DISABLED",
                "message": "The commercial journey is available only in the isolated simulator",
            },
        )


def _configured_products() -> list[dict]:
    try:
        return commercial_product_catalog()
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Commercial product configuration is invalid",
        ) from error


def _assisted_product_enabled(product: dict) -> bool:
    return (
        not simulator_ready()
        and product["id"] in {"professional", "monitor"}
        and product["configured_enabled"]
        and real_express_gate()["enabled"]
    )


def _configured_limits() -> dict[str, int]:
    try:
        return commercial_limits()
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Commercial limit configuration is invalid",
        ) from error


def _bearer_token(authorization: str | None, variable: str, purpose: str) -> str:
    expected = os.environ.get(variable, "").strip()
    if len(expected) < 32:
        raise HTTPException(
            status_code=503,
            detail=f"{purpose} authentication is not configured",
        )
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied.strip(), expected)
    ):
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return expected


def require_wordpress_auth(
    authorization: str | None = Header(default=None),
) -> str:
    return _bearer_token(
        authorization,
        "GEOGRID_WORDPRESS_API_TOKEN",
        "WordPress API",
    )


def _require_operator(
    authorization: str | None,
    operator_id: str | None,
) -> str:
    _bearer_token(
        authorization,
        "GEOGRID_COMMERCE_OPERATOR_TOKEN",
        "Commerce operator",
    )
    if not operator_id or not _OPERATOR_ID.fullmatch(operator_id):
        raise HTTPException(status_code=401, detail="A valid operator ID is required")
    allowed = {
        item.strip()
        for item in os.environ.get("GEOGRID_COMMERCE_OPERATOR_IDS", "").split(",")
        if item.strip()
    }
    if not allowed or operator_id not in allowed:
        raise HTTPException(status_code=403, detail="Operator is not authorized")
    return operator_id


def _admin_cookie_value(operator_id: str, expires_at: int) -> str:
    secret = os.environ.get("GEOGRID_COMMERCE_OPERATOR_TOKEN", "").strip()
    payload = f"{operator_id}|{expires_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).digest()
    return (
        encoded.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def _require_admin_session(request: Request) -> str:
    secret = os.environ.get("GEOGRID_COMMERCE_OPERATOR_TOKEN", "").strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Commerce operator authentication is not configured")
    value = request.cookies.get("zuri_commerce_admin", "")
    try:
        encoded_text, signature_text = value.split(".", 1)
        encoded = encoded_text.encode("ascii")
        signature = base64.urlsafe_b64decode(
            signature_text + "=" * (-len(signature_text) % 4)
        )
        expected = hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        operator_id, expires_text = payload.decode("utf-8").split("|", 1)
        expires_at = int(expires_text)
    except (ValueError, UnicodeError) as error:
        raise HTTPException(status_code=401, detail="Administrator session is invalid") from error
    if expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="Administrator session has expired")
    allowed = {
        item.strip()
        for item in os.environ.get("GEOGRID_COMMERCE_OPERATOR_IDS", "").split(",")
        if item.strip()
    }
    if not _OPERATOR_ID.fullmatch(operator_id) or operator_id not in allowed:
        raise HTTPException(status_code=403, detail="Operator is not authorized")
    return operator_id


def _require_secure_admin_request(request: Request) -> bool:
    secure_cookie = os.environ.get(
        "ZURI_COMMERCE_ADMIN_COOKIE_SECURE", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    allow_insecure_http = os.environ.get(
        "ZURI_COMMERCE_ADMIN_ALLOW_INSECURE_HTTP", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if request.url.scheme != "https" and not allow_insecure_http:
        raise HTTPException(
            status_code=403,
            detail="Administrator login requires HTTPS",
        )
    if not secure_cookie and not allow_insecure_http:
        raise HTTPException(
            status_code=503,
            detail="Insecure administrator cookies are permitted only in isolated development",
        )
    return secure_cookie


def _check_capability(lead_id: str, status_token: str) -> dict:
    if not _TOKEN.fullmatch(status_token):
        raise HTTPException(status_code=422, detail="Invalid status capability")
    lead = commercial_store.get_lead(lead_id)
    if not lead or not hmac.compare_digest(
        lead["status_token_hash"], _digest(status_token)
    ):
        raise HTTPException(status_code=404, detail="Lead or capability not found")
    return lead


def _check_request_id(request_id: uuid.UUID) -> None:
    if request_id.version != 4:
        raise HTTPException(status_code=422, detail="request_id must be UUIDv4")


def _validate_lead(body: CommercialLeadIn) -> tuple[dict, str | None]:
    _check_request_id(body.request_id)
    if not _TOKEN.fullmatch(body.status_token):
        raise HTTPException(status_code=422, detail="Invalid status capability")
    if not body.privacy_acknowledged:
        raise HTTPException(status_code=422, detail="Privacy notice acknowledgement is required")
    email = body.email.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(status_code=422, detail="Invalid email")
    phone = re.sub(r"[^\d+]", "", body.whatsapp)
    if not _PHONE.fullmatch(phone):
        raise HTTPException(status_code=422, detail="Invalid WhatsApp number")
    campaign_id = body.campaign_id.strip()
    if campaign_id not in _campaign_ids():
        raise HTTPException(status_code=422, detail="Invalid campaign")
    try:
        _, policy_version = _configured_policy()
    except HTTPException as error:
        raise HTTPException(
            status_code=503,
            detail="Privacy policy is not configured",
        ) from error
    business_name = body.business_name.strip()
    city = body.city.strip()
    state = body.state.strip().upper()
    contact_name = body.contact_name.strip()
    if any(len(value) < 2 for value in (business_name, city, state, contact_name)):
        raise HTTPException(status_code=422, detail="Business, location, and contact details are required")
    maps_url = body.google_maps_url.strip() if body.google_maps_url else ""
    search_request_id = body.search_request_id or uuid.uuid4()
    if body.business_search:
        if (
            not body.business_confirmed
            or body.search_request_id is None
            or body.business_search.request_id != body.search_request_id
        ):
            raise HTTPException(
                status_code=422,
                detail="Confirmed business search does not match the lead",
            )
        search = body.business_search
    else:
        search = BusinessSearchIn(
            request_id=search_request_id,
            business_name=business_name,
            city=city,
            state=state,
            address=body.address,
            google_maps_url=maps_url or None,
        )
    query = _query_from_input(search)
    if maps_url:
        try:
            parsed_maps_url = urlparse(maps_url)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid Google Maps URL") from error
        maps_host = (parsed_maps_url.hostname or "").lower()
        if parsed_maps_url.scheme != "https" or maps_host not in business_finder._GOOGLE_MAPS_HOSTS:
            raise HTTPException(status_code=422, detail="Invalid Google Maps URL")
    confirmation_hash = None
    lead_type = "assisted"
    if body.business_confirmed:
        if body.search_request_id is None or body.search_request_id.version != 4:
            raise HTTPException(status_code=422, detail="A confirmed business search is required")
        if not body.confirmation_token:
            raise HTTPException(status_code=422, detail="Explicit confirmation token is required")
        claims = _decode_token(body.confirmation_token, _signing_secret())
        if (
            claims.get("stage") != "confirmed"
            or claims.get("search_request_id") != str(body.search_request_id)
            or not hmac.compare_digest(
                claims.get("query_hash", ""),
                _query_hash(query, _signing_secret()),
            )
        ):
            raise HTTPException(status_code=409, detail="Confirmation does not match the submitted business")
        lead_type = "confirmed"
        confirmation_hash = _digest(body.confirmation_token)
    elif body.confirmation_token or body.search_request_id:
        raise HTTPException(
            status_code=422,
            detail="Assisted leads cannot include a business confirmation token",
        )
    payload = {
        "request_id": str(body.request_id),
        "lead_type": lead_type,
        "business_confirmed": body.business_confirmed,
        "search_request_id": (
            str(body.search_request_id) if body.search_request_id else None
        ),
        "business_name": business_name,
        "city": city,
        "state": state,
        "address": body.address.strip() if body.address else None,
        "google_maps_url": maps_url or None,
        "contact_name": contact_name,
        "email": email,
        "whatsapp": phone,
        "campaign_id": campaign_id,
        "privacy_policy_version": policy_version,
        "marketing_consent": body.marketing_consent,
    }
    return payload, confirmation_hash


def _public_diagnostic(diagnostic: dict) -> dict:
    scan = db.get_scan(diagnostic["scan_id"], with_points=False) if diagnostic.get("scan_id") else None
    status = (
        scan["status"]
        if scan
        else diagnostic.get("status", "reserved")
    )
    return {
        "diagnostic_id": diagnostic["id"],
        "request_id": diagnostic["request_id"],
        "product_id": diagnostic["product_id"],
        "grid_size": diagnostic["grid_size"],
        "keyword": diagnostic["keyword"],
        "status": status,
        "progress": scan.get("progress", 0) if scan else 0,
        "total": scan.get("total", diagnostic["grid_size"] ** 2) if scan else diagnostic["grid_size"] ** 2,
    }


def _commercial_contact_url() -> str | None:
    value = os.environ.get("ZURI_COMMERCIAL_CONTACT_URL", "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise HTTPException(status_code=503, detail="Commercial contact URL is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise HTTPException(status_code=503, detail="Commercial contact URL is invalid")
    return value


@router.get("/api/commercial/config")
def commercial_config():
    products = _configured_products()
    limits = _configured_limits()
    simulation_active = simulator_ready()
    real_gate = real_express_gate()
    for product in products:
        product["assisted_request_enabled"] = _assisted_product_enabled(product)
    return {
        "mode": "simulated" if simulation_active else "disabled",
        "simulator_enabled": simulation_active,
        "real_provider_enabled": real_gate["enabled"],
        "real_express_enabled": real_gate["enabled"],
        "real_express_blockers": real_gate["blockers"],
        "commercial_diagnostics_gate": landing_diagnostics_gate(),
        "prospecting_export_enabled": prospecting_storage_approved(),
        "products": products,
        "limits": limits,
        "contact_url": _commercial_contact_url(),
        "wordpress_server_auth_configured": len(os.environ.get("GEOGRID_WORDPRESS_API_TOKEN", "").strip()) >= 32,
    }


@router.get("/api/commercial/admin", response_class=HTMLResponse)
def commercial_admin_page():
    page = Path(__file__).parent / "static" / "commercial-admin.html"
    return HTMLResponse(
        page.read_text(encoding="utf-8"),
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; style-src 'self'; "
                "script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/commercial/admin/login")
async def commercial_admin_login(body: AdminLoginIn, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "admin")
    secure_cookie = _require_secure_admin_request(request)
    operator_id = _require_operator(
        f"Bearer {body.operator_token}",
        body.operator_id,
    )
    expires_at = int(time.time()) + 1800
    response = JSONResponse(
        {"authenticated": True, "operator_id": operator_id},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        "zuri_commerce_admin",
        _admin_cookie_value(operator_id, expires_at),
        max_age=1800,
        path="/api/commercial/admin",
        secure=secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/api/commercial/admin/session")
def commercial_admin_session(request: Request):
    return {
        "authenticated": True,
        "operator_id": _require_admin_session(request),
    }


@router.post("/api/commercial/admin/logout")
def commercial_admin_logout(request: Request):
    _check_origin(request)
    _require_admin_session(request)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        "zuri_commerce_admin",
        path="/api/commercial/admin",
        secure=os.environ.get(
            "ZURI_COMMERCE_ADMIN_COOKIE_SECURE", "true"
        ).strip().lower() in {"1", "true", "yes", "on"},
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/api/commercial/admin/leads")
def commercial_admin_leads(
    request: Request,
    q: str = Query(default="", max_length=120),
    marketing_consent: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    operator_id = _require_admin_session(request)
    leads = prospecting.list_leads(
        query=q,
        marketing_consent=marketing_consent,
        limit=limit,
    )
    for lead in leads:
        lead["commercial_qualification"] = (
            "priority"
            if lead["marketing_consent"]
            else "not_export_eligible"
        )
    return {
        "operator_id": operator_id,
        "items": leads,
        "export_policy": "explicit marketing consent required",
    }


@router.get("/api/commercial/admin/prospects")
def commercial_admin_prospects(
    request: Request,
    q: str = Query(default="", max_length=120),
    qualification: Literal["priority", "standard", "low", "simulation"] | None = None,
    provider: str | None = Query(default=None, max_length=100),
    export_eligible: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    operator_id = _require_admin_session(request)
    return {
        "operator_id": operator_id,
        "items": prospecting.list_prospects(
            query=q,
            qualification=qualification,
            provider=provider,
            export_eligible=export_eligible,
            limit=limit,
        ),
        "prospecting_export_enabled": prospecting_storage_approved(),
    }


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"} or text[:1] in {
        "\t",
        "\r",
        "\n",
    }:
        return "'" + text
    return text


def _csv_response(items: list[dict], columns: tuple[str, ...], filename: str) -> Response:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(_csv_cell(item.get(column)) for column in columns)
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/commercial/admin/export/{record_type}.csv")
def commercial_admin_export(
    record_type: Literal["leads", "prospects"],
    request: Request,
):
    _require_admin_session(request)
    if record_type == "leads":
        return _csv_response(
            prospecting.exportable_leads(),
            (
                "business_name",
                "city",
                "state",
                "contact_name",
                "email",
                "whatsapp",
                "campaign_id",
                "created_at",
                "marketing_consent",
            ),
            "zuri-leads.csv",
        )
    if not prospecting_storage_approved():
        raise HTTPException(
            status_code=403,
            detail="Prospecting data export is disabled until source-use approval is configured",
        )
    return _csv_response(
        prospecting.exportable_prospects(),
        (
            "name",
            "address",
            "category",
            "place_id",
            "cid",
            "first_seen_at",
            "last_seen_at",
            "observation_count",
            "best_rank",
            "record_status",
            "qualification",
            "source_provider",
        ),
        "zuri-prospects.csv",
    )


@router.post("/api/commercial/leads", status_code=201)
async def register_commercial_lead(body: CommercialLeadIn, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "leads")
    payload, confirmation_hash = _validate_lead(body)
    if body.website:
        return {
            "accepted": True,
            "request_id": payload["request_id"],
            "status": "discarded",
            "duplicate": False,
        }
    payload_hash = _canonical_hash(
        {
            **payload,
            "confirmation_token_hash": confirmation_hash,
            "status_token_hash": _digest(body.status_token),
        }
    )
    try:
        lead, created = commercial_store.create_lead(
            payload,
            payload_hash=payload_hash,
            status_token_hash=_digest(body.status_token),
            confirmation_token_hash=confirmation_hash,
        )
    except commercial_store.CommercialConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "accepted": True,
        "request_id": lead["request_id"],
        "lead_id": lead["id"],
        "status_token": body.status_token,
        "business_confirmed": bool(lead["business_confirmed"]),
        "lead_type": lead["lead_type"],
        "duplicate": not created,
    }


@router.post("/api/commercial/diagnostics", status_code=202)
async def create_commercial_diagnostic(body: CommercialDiagnosticIn, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "leads")
    simulation_active = simulator_ready()
    if not simulation_active:
        gate = real_express_gate()
        if not gate["enabled"]:
            raise HTTPException(
                status_code=503,
                detail={
                    "category": "REAL_EXPRESS_DISABLED",
                    "message": "The real Express queue is blocked pending explicit operational approvals",
                    "blockers": gate["blockers"],
                },
            )
    _check_request_id(body.request_id)
    _check_request_id(body.search.request_id)
    if body.confirmed is not True:
        raise HTTPException(status_code=422, detail="Confirm the selected business before diagnosis")
    lead = _check_capability(str(body.lead_id), body.status_token)
    if (
        not lead["business_confirmed"]
        or lead["lead_type"] != "confirmed"
        or lead["confirmation_token_hash"] is None
        or lead["business_search_request_id"] != str(body.search.request_id)
        or not hmac.compare_digest(
            lead["confirmation_token_hash"], _digest(body.confirmation_token)
        )
    ):
        raise HTTPException(status_code=409, detail="A confirmed business is required")
    products = _configured_products()
    product = next(item for item in products if item["id"] == body.product_id)
    if simulation_active and not product["diagnostic_enabled"]:
        raise HTTPException(status_code=503, detail="This product is not available")
    if body.product_id != "express":
        raise HTTPException(
            status_code=409,
            detail="Professional diagnostics require a manually confirmed payment and operator release",
        )
    query = _query_from_input(body.search)
    claims = _decode_token(body.confirmation_token, _signing_secret())
    if (
        claims.get("stage") != "confirmed"
        or claims.get("search_request_id") != str(body.search.request_id)
        or not hmac.compare_digest(
            claims.get("query_hash", ""),
            _query_hash(query, _signing_secret()),
        )
    ):
        raise HTTPException(status_code=409, detail="Confirmation does not match this business")
    if not simulation_active and (
        claims.get("provider_id") != "google_places"
        or not claims.get("reference_id")
        or not isinstance(claims.get("nonce"), str)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "category": "SOURCE_NOT_AUTHORIZED",
                "message": "Real Express requires the exact, confirmed Google Place ID",
            },
        )
    keyword = body.keyword.strip()
    if len(keyword) < 2:
        raise HTTPException(status_code=422, detail="Keyword must contain at least two characters")
    grid_size = product["grid_size"]
    request_hash = _canonical_hash(
        {
            "lead_id": str(body.lead_id),
            "product_id": body.product_id,
            "business_confirmation_hash": lead["confirmation_token_hash"],
            "search_request_id": str(body.search.request_id),
            "confirmed_provider_id": claims.get("provider_id"),
            "confirmed_reference_id": claims.get("reference_id"),
            "keyword": keyword,
        }
    )
    diagnostic_id = str(uuid.uuid4())
    try:
        diagnostic, _ = commercial_store.reserve_diagnostic(
            diagnostic_id=diagnostic_id,
            request_id=str(body.request_id),
            request_hash=request_hash,
            lead_id=str(body.lead_id),
            product_id=body.product_id,
            grid_size=grid_size,
            keyword=keyword,
            confirmed_provider_id=claims.get("provider_id"),
            confirmed_reference_id=claims.get("reference_id"),
            confirmation_nonce_hash=(
                _digest(claims["nonce"])
                if not simulation_active
                else None
            ),
        )
    except commercial_store.CommercialCapacityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Commercial limit configuration is invalid",
        ) from error
    except (commercial_store.CommercialConflictError, sqlite3.IntegrityError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not simulation_active:
        if diagnostic["status"] in {"queued", "running", "done", "failed"}:
            return {
                "accepted": diagnostic["status"] != "failed",
                "diagnostic": _public_diagnostic(diagnostic),
                "data_mode": "standard_normal",
                "queued_via": "n8n",
                "provider_calls": 0,
                "duplicate": True,
            }
        place_id = str(claims["reference_id"])
        maps_url = (
            "https://www.google.com/maps/search/?api=1&query_place_id="
            + quote(place_id, safe="")
        )
        try:
            await submit_landing_lead(
                LeadSubmission(
                    request_id=body.request_id,
                    status_token=body.status_token,
                    contact_name=lead["contact_name"],
                    company_name=lead["business_name"],
                    email=lead["email"],
                    whatsapp=lead["whatsapp"],
                    city=lead["city"],
                    state=lead["state"],
                    google_maps_url=maps_url,
                    keyword=keyword,
                    campaign_id=lead["campaign_id"],
                    privacy_acknowledged=True,
                    marketing_consent=bool(lead["marketing_consent"]),
                ),
                request,
            )
        except HTTPException:
            raise
        try:
            diagnostic = commercial_store.mark_diagnostic_queued(
                str(body.request_id)
            )
        except commercial_store.CommercialConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "accepted": True,
            "diagnostic": _public_diagnostic(diagnostic),
            "data_mode": "standard_normal",
            "queued_via": "n8n",
            "provider_calls": 0,
            "duplicate": False,
        }
    diagnostic_body = BusinessDiagnosticIn(
        request_id=body.request_id,
        confirmation_token=body.confirmation_token,
        confirmed=True,
        search=body.search,
        keywords=[keyword],
        grid_size=grid_size,
        spacing_km=1.0,
    )
    result = await start_business_diagnostic(diagnostic_body, request)
    scan_ids = result["scan_ids"]
    if len(scan_ids) != 1:
        raise HTTPException(status_code=502, detail="Simulator returned an invalid scan count")
    scan = db.get_scan(int(scan_ids[0]), with_points=False)
    if not scan:
        raise HTTPException(status_code=502, detail="Simulator did not persist the diagnostic scan")
    try:
        diagnostic, _ = commercial_store.create_diagnostic(
            request_id=str(body.request_id),
            request_hash=request_hash,
            lead_id=str(body.lead_id),
            product_id=body.product_id,
            business_id=scan["business_id"],
            scan_id=int(scan_ids[0]),
            grid_size=grid_size,
            keyword=keyword,
        )
    except commercial_store.CommercialConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "accepted": True,
        "diagnostic": _public_diagnostic(diagnostic),
        "data_mode": "synthetic",
        "provider_calls": 0,
        "duplicate": False,
    }


@router.post("/api/commercial/leads/{lead_id}/status")
async def commercial_status(lead_id: uuid.UUID, body: CommercialCapabilityIn, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "status")
    if str(lead_id) != str(body.lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = _check_capability(str(lead_id), body.status_token)
    diagnostics = commercial_store.diagnostics_for_lead(str(lead_id))
    return {
        "lead_id": str(lead_id),
        "lead_type": lead["lead_type"],
        "business_confirmed": bool(lead["business_confirmed"]),
        "diagnostics": [_public_diagnostic(item) for item in diagnostics],
        "orders": [
            {
                "order_id": order["id"],
                "product_id": order["product_id"],
                "amount_brl": order["amount_brl"],
                "status": order["status"],
                "diagnostic_id": order["diagnostic_id"],
            }
            for order in commercial_store.list_orders_for_lead(str(lead_id))
        ],
    }


@router.post("/api/commercial/diagnostics/{diagnostic_id}/report")
async def commercial_report(
    diagnostic_id: uuid.UUID,
    body: CommercialCapabilityIn,
    request: Request,
):
    _check_origin(request)
    await _check_rate_limit(request, "report")
    lead = _check_capability(str(body.lead_id), body.status_token)
    diagnostic = commercial_store.get_diagnostic(str(diagnostic_id))
    if not diagnostic or diagnostic["lead_id"] != lead["id"]:
        raise HTTPException(status_code=404, detail="Diagnostic not found")
    if not diagnostic["scan_id"]:
        raise HTTPException(status_code=409, detail="Diagnostic is still being prepared")
    scan = db.get_scan(diagnostic["scan_id"])
    if not scan:
        raise HTTPException(status_code=404, detail="Diagnostic scan not found")
    if scan["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail={
                "category": "REPORT_NOT_READY",
                "status": scan["status"],
                "progress": scan["progress"],
                "total": scan["total"],
            },
        )
    prospecting.capture_completed_scan(diagnostic["scan_id"])
    data_mode = {
        "simulator": "synthetic",
        "dataforseo_standard": "standard_normal",
    }.get(scan.get("result_provider"), "live")
    analysis = main.scan_analysis(
        diagnostic["scan_id"],
        category=None,
        related_category=None,
    )
    coverage = scan_coverage(scan["points"], scan["total"])
    products = {item["id"]: item for item in _configured_products()}
    if diagnostic["product_id"] == "express":
        return {
            "diagnostic_id": str(diagnostic_id),
            "report_type": "express",
            "business_name": scan["business_name"],
            "keyword": scan["keyword"],
            "grid_size": scan["grid_size"],
            "coverage": coverage,
            "metrics": {
                "arp": analysis["metrics"]["arp"],
                "found_pct": analysis["metrics"]["found_pct"],
                "ivl": analysis["metrics"]["ivl"],
            },
            "incomplete": coverage["coverage_pct"] < 100,
            "offer": {
                "product_id": "professional",
                "price_brl": products["professional"]["price_brl"],
                "checkout_url": products["professional"]["checkout_url"],
                "payment_confirmation": "manual_operator",
            },
            "contact_action": _commercial_contact_url(),
            "data_mode": data_mode,
        }
    return {
        "diagnostic_id": str(diagnostic_id),
        "report_type": "professional",
        "business_name": scan["business_name"],
        "keyword": scan["keyword"],
        "grid_size": scan["grid_size"],
        "coverage": coverage,
        "metrics": analysis["metrics"],
        "indicators": {
            "icm": analysis["icm"],
            "icpb_v2": analysis["icpb_v2"],
            "ice": analysis["ice"],
            "iop": analysis["iop"],
            "ida_average": analysis["ida_average"],
            "ida_coverage_pct": analysis["ida_coverage_pct"],
        },
        "points": [
            {
                "row": point["row"],
                "col": point["col"],
                "lat": point["lat"],
                "lng": point["lng"],
                "rank": point["rank"],
                "status": point["status"],
            }
            for point in analysis["points"]
        ],
        "incomplete": coverage["coverage_pct"] < 100,
        "data_mode": data_mode,
    }


@router.post("/api/commercial/orders", status_code=201)
async def create_commercial_order(body: CommercialOrderIn, request: Request):
    _check_origin(request)
    await _check_rate_limit(request, "leads")
    _check_request_id(body.request_id)
    lead = _check_capability(str(body.lead_id), body.status_token)
    products = {item["id"]: item for item in _configured_products()}
    product = products[body.product_id]
    assisted = _assisted_product_enabled(product)
    if not product["available"] and not assisted:
        raise HTTPException(status_code=503, detail="This product is not available")
    order_hash = _canonical_hash(
        {
            "lead_id": str(body.lead_id),
            "source_diagnostic_id": str(body.source_diagnostic_id),
            "product_id": body.product_id,
            "amount_brl": product["price_brl"],
        }
    )
    order_id = str(uuid.uuid4())
    try:
        order, created = commercial_store.create_order(
            order_id=order_id,
            request_id=str(body.request_id),
            request_hash=order_hash,
            lead_id=lead["id"],
            source_diagnostic_id=str(body.source_diagnostic_id),
            product_id=body.product_id,
            amount_brl=product["price_brl"],
            assisted=assisted,
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="A product order already exists for this lead",
        ) from error
    except commercial_store.CommercialConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "order_id": order["id"],
        "product_id": order["product_id"],
        "amount_brl": order["amount_brl"],
        "status": order["status"],
        "checkout_url": None if assisted else product["checkout_url"],
        "checkout_is_payment_proof": False,
        "fulfillment": "assisted" if order["status"] == "assisted" else "payment",
        "duplicate": not created,
    }


@router.post("/api/commercial/operator/orders/{order_id}/confirm-payment")
def confirm_commercial_payment(
    order_id: uuid.UUID,
    body: OperatorPaymentIn,
    authorization: str | None = Header(default=None),
    operator_id: str | None = Header(default=None, alias="X-Operator-ID"),
):
    actor = _require_operator(authorization, operator_id)
    _require_simulator()
    payment_reference = body.payment_reference.strip()
    if any(ord(char) < 32 for char in payment_reference):
        raise HTTPException(status_code=422, detail="Invalid payment reference")
    try:
        order = commercial_store.confirm_order_payment(
            str(order_id),
            payment_reference=payment_reference,
            operator_id=actor,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Order not found") from error
    except commercial_store.CommercialConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "order_id": order["id"],
        "status": order["status"],
        "payment_confirmed_at": order["payment_confirmed_at"],
        "operator_id": actor,
        "idempotent": order["status"] == "payment_confirmed",
    }


@router.post("/api/commercial/operator/orders/{order_id}/release")
async def release_commercial_order(
    order_id: uuid.UUID,
    authorization: str | None = Header(default=None),
    operator_id: str | None = Header(default=None, alias="X-Operator-ID"),
):
    actor = _require_operator(authorization, operator_id)
    _require_simulator()
    professional = next(
        item
        for item in _configured_products()
        if item["id"] == "professional"
    )
    try:
        diagnostic, created = commercial_store.release_order(
            str(order_id),
            operator_id=actor,
            grid_size=professional["grid_size"],
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Order not found") from error
    except commercial_store.CommercialCapacityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Commercial limit configuration is invalid",
        ) from error
    except commercial_store.CommercialConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if created and diagnostic["scan_id"]:
        import asyncio

        asyncio.create_task(main._run_queued([diagnostic["scan_id"]], simulated=True))
    return {
        "order_id": str(order_id),
        "diagnostic": _public_diagnostic(diagnostic),
        "duplicate": not created,
    }


@router.get("/api/commercial/operator/orders/{order_id}")
def operator_order_details(
    order_id: uuid.UUID,
    authorization: str | None = Header(default=None),
    operator_id: str | None = Header(default=None, alias="X-Operator-ID"),
):
    actor = _require_operator(authorization, operator_id)
    _require_simulator()
    order = commercial_store.get_order(str(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "order_id": order["id"],
        "lead_id": order["lead_id"],
        "source_diagnostic_id": order["source_diagnostic_id"],
        "diagnostic_id": order["diagnostic_id"],
        "product_id": order["product_id"],
        "amount_brl": order["amount_brl"],
        "status": order["status"],
        "payment_reference_recorded": bool(order["payment_reference"]),
        "events": commercial_store.order_events(order["id"]),
        "operator_id": actor,
    }


async def _wordpress_rate_limit(request: Request, bucket: str) -> None:
    _check_origin(request)
    await _check_rate_limit(request, bucket)


@wordpress_router.post("/businesses/search")
async def wordpress_search(
    body: BusinessSearchIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    await _wordpress_rate_limit(request, "leads")
    _require_simulator()
    return await search_business(body, request)


@wordpress_router.post("/businesses/confirm")
async def wordpress_confirm(
    body: WordPressConfirmationIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    await _wordpress_rate_limit(request, "leads")
    return confirm_business(
        BusinessConfirmIn(
            request_id=body.request_id,
            candidate_token=body.candidate_token,
            confirmed=body.confirmed,
        )
    )


@wordpress_router.post("/leads", status_code=201)
async def wordpress_register_lead(
    body: CommercialLeadIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    return await register_commercial_lead(body, request)


@wordpress_router.post("/diagnostics", status_code=202)
async def wordpress_start_diagnostic(
    body: CommercialDiagnosticIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    return await create_commercial_diagnostic(body, request)


@wordpress_router.post("/leads/{lead_id}/status")
async def wordpress_status(
    lead_id: uuid.UUID,
    body: CommercialCapabilityIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    return await commercial_status(lead_id, body, request)


@wordpress_router.post("/diagnostics/{diagnostic_id}/report")
async def wordpress_report(
    diagnostic_id: uuid.UUID,
    body: CommercialCapabilityIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    return await commercial_report(diagnostic_id, body, request)


@wordpress_router.post("/orders", status_code=201)
async def wordpress_create_order(
    body: CommercialOrderIn,
    request: Request,
    _: str = Depends(require_wordpress_auth),
):
    return await create_commercial_order(body, request)


@wordpress_router.get("/payments/{order_id}")
def wordpress_payment_status(
    order_id: uuid.UUID,
    _: str = Depends(require_wordpress_auth),
):
    order = commercial_store.get_order(str(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "order_id": order["id"],
        "product_id": order["product_id"],
        "amount_brl": order["amount_brl"],
        "status": order["status"],
        "payment_reference_recorded": bool(order["payment_reference"]),
        "payment_confirmed_at": order["payment_confirmed_at"],
        "diagnostic_id": order["diagnostic_id"],
    }
