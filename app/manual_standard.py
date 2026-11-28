"""Operator intake for the existing Standard processor queue."""

import csv
import hashlib
import json
import os
from decimal import Decimal
from io import StringIO
from typing import Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from . import db
from .commercial import landing_diagnostics_gate
from .commercial_api import _require_admin_session, _require_secure_admin_request
from .grid import make_grid
from .landing import _check_origin
from .live_finance import LiveFinanceError, _connect, _lock_shared_ledger

router = APIRouter(prefix="/api/commercial/admin/standard-scans")
_EVENT = "manual-standard"


class CompanyScans(BaseModel):
    business_id: int = Field(gt=0)
    keywords: list[str] = Field(min_length=1, max_length=10)
    grid_size: Literal[3, 5, 7, 9] = 7
    spacing_km: float = Field(default=1, gt=0, le=50)


class StandardBatch(BaseModel):
    modality: Literal["normal", "priority"] = "normal"
    companies: list[CompanyScans] = Field(default_factory=list, max_length=10)
    csv_text: str | None = Field(default=None, max_length=24000)
    approved_total_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=6)


def _operator(request: Request) -> str:
    _check_origin(request)
    _require_secure_admin_request(request)
    return _require_admin_session(request)


def _rows(body: StandardBatch) -> list[tuple]:
    companies = body.companies
    if body.csv_text is not None:
        if companies:
            raise HTTPException(422, "Informe empresas ou CSV.")
        try:
            reader = csv.DictReader(StringIO(body.csv_text.lstrip("\ufeff")), strict=True)
            if reader.fieldnames != ["business_id", "keywords", "grid_size", "spacing_km"]:
                raise ValueError("header")
            companies = [
                CompanyScans(
                    business_id=row["business_id"], keywords=row["keywords"].split("|"),
                    grid_size=int(row["grid_size"]), spacing_km=row["spacing_km"],
                )
                for row in reader
            ]
        except (ValueError, AttributeError, ValidationError, csv.Error) as error:
            raise HTTPException(422, "CSV inválido.") from error
    if not 1 <= len(companies) <= 10:
        raise HTTPException(422, "Informe de uma a dez empresas.")
    rows = []
    seen = set()
    for company in companies:
        business = db.get_business(company.business_id)
        if not business:
            raise HTTPException(404, "Empresa cadastrada não encontrada.")
        try:
            grid = make_grid(business["lat"], business["lng"], company.grid_size, company.spacing_km)
        except (TypeError, ValueError, OverflowError) as error:
            raise HTTPException(422, "Coordenadas inválidas.") from error
        if any(not (-90 <= p.lat <= 90 and -180 <= p.lng <= 180) for p in grid):
            raise HTTPException(422, "A grade excede os limites geográficos.")
        for raw in company.keywords:
            keyword = raw.strip()
            identity = (company.business_id, keyword.casefold())
            if not 2 <= len(keyword) <= 200 or identity in seen:
                raise HTTPException(422, "Palavra-chave inválida ou duplicada na empresa.")
            seen.add(identity)
            rows.append((company.business_id, keyword, company.grid_size, company.spacing_km, business["name"]))
    return rows


def _quote(connection, modality: str, rows: list[tuple]) -> dict:
    control = connection.execute("""
        SELECT enabled, manual_standard_enabled, estimated_task_cost_usd,
               estimated_priority_task_cost_usd, max_pending_tasks
        FROM geogrid_budget WHERE scope_type = 'system' AND scope_key = 'default'
    """).fetchone()
    cost_field = "estimated_priority_task_cost_usd" if modality == "priority" else "estimated_task_cost_usd"
    price = control[cost_field] if control else None
    if price is not None and (not price.is_finite() or price <= 0):
        price = None
    total_points = sum(row[2] ** 2 for row in rows)
    enabled = bool(
        price and control["enabled"] and control["manual_standard_enabled"]
        and (control["max_pending_tasks"] or 0) >= max(row[2] ** 2 for row in rows)
        and os.environ.get("GEOGRID_MANUAL_STANDARD_ENABLED") == "true"
        and landing_diagnostics_gate()["enabled"]
    )
    return {
        "enabled": enabled,
        "modality": modality,
        "queries": total_points,
        "researches": len(rows),
        "unit_cost_usd": price,
        "total_usd": total_points * price if price else None,
    }


@router.post("/quote")
def quote_batch(body: StandardBatch, request: Request):
    _operator(request)
    rows = _rows(body)
    try:
        with _connect() as connection:
            quote = _quote(connection, body.modality, rows)
            return {key: str(value) if isinstance(value, Decimal) else value
                    for key, value in quote.items()}
    except (LiveFinanceError, psycopg.Error) as error:
        raise HTTPException(503, "Fila financeira indisponível.") from error


@router.post("", status_code=202)
def enqueue_batch(body: StandardBatch, request: Request, idempotency_key: UUID = Header()):
    operator = _operator(request)
    rows = _rows(body)
    fingerprint = hashlib.sha256(json.dumps(
        [body.modality, [row[:4] for row in rows]], ensure_ascii=False,
    ).encode()).hexdigest()
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            existing = connection.execute("""
                SELECT operator_id, fingerprint FROM geogrid_manual_standard_batches WHERE batch_id = %s
            """, (idempotency_key,)).fetchone()
            if existing:
                if existing["operator_id"] != operator or existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Chave de idempotência já utilizada.")
                return {"batch_id": idempotency_key}
            quote = _quote(connection, body.modality, rows)
            if not quote["enabled"]:
                raise HTTPException(503, "Standard manual indisponível.")
            if body.approved_total_usd != quote["total_usd"]:
                raise HTTPException(409, "Confirme a estimativa atual antes de enfileirar.")
            event = connection.execute("""
                SELECT 1 FROM geogrid_budget
                WHERE scope_type = 'event' AND scope_key = %s AND cap_amount_usd > 0
            """, (_EVENT,)).fetchone()
            if not event:
                raise HTTPException(503, "Orçamento do evento Standard manual indisponível.")
            connection.execute("""
                INSERT INTO geogrid_manual_standard_batches (batch_id, operator_id, fingerprint)
                VALUES (%s, %s, %s)
            """, (idempotency_key, operator, fingerprint))
            for index, (business_id, keyword, size, spacing, name) in enumerate(rows):
                request_id = uuid5(idempotency_key, str(index))
                connection.execute("""
                    INSERT INTO geogrid_requests (
                        request_id, idempotency_key, payload_fingerprint, campaign_id,
                        contact_name, company_name, email, whatsapp, city, state,
                        google_maps_url, keyword, consent_at, total_points,
                        manual_business_id, manual_batch_id, grid_size, spacing_km,
                        standard_priority, manual_unit_cost_ceiling_usd
                    ) VALUES (%s,%s,%s,%s,'',%s,'','','','','',%s,now(),%s,%s,%s,%s,%s,%s,%s)
                """, (request_id, f"manual-standard:{request_id}", fingerprint, _EVENT, name,
                      keyword, size * size, business_id, idempotency_key, size, spacing,
                      2 if body.modality == "priority" else 1, quote["unit_cost_usd"]))
    except (LiveFinanceError, psycopg.Error) as error:
        raise HTTPException(503, "Fila financeira indisponível.") from error
    return {"batch_id": idempotency_key}


@router.get("/{batch_id}")
def read_batch(batch_id: UUID, request: Request):
    operator = _operator(request)
    try:
        with _connect() as connection:
            batch = connection.execute("""
                SELECT batch_id FROM geogrid_manual_standard_batches WHERE batch_id = %s AND operator_id = %s
            """, (batch_id, operator)).fetchone()
            if not batch:
                raise HTTPException(404, "Lote não encontrado.")
            rows = connection.execute("""
                SELECT r.request_id, r.manual_business_id, r.company_name, r.keyword, r.status, r.scan_id, r.total_points,
                       r.budget_reserved_usd, r.actual_cost_usd, r.actual_cost_known,
                       (SELECT count(*) FROM geogrid_tasks t WHERE t.request_id = r.request_id
                        AND t.state IN ('done','no_results','failed')) AS completed_points
                FROM geogrid_requests r WHERE r.manual_batch_id = %s ORDER BY r.created_at, r.request_id
            """, (batch_id,)).fetchall()
            return {"batch_id": batch_id, "researches": rows}
    except (LiveFinanceError, psycopg.Error) as error:
        raise HTTPException(503, "Fila financeira indisponível.") from error


@router.get("/{batch_id}/companies/{business_id}/pdf")
async def consolidated_pdf(batch_id: UUID, business_id: int, request: Request):
    from .server_reports import basic_pdf

    operator = _operator(request)
    try:
        with _connect() as connection:
            rows = connection.execute("""
                SELECT DISTINCT ON (lower(r.keyword)) r.scan_id, r.status, r.keyword
                FROM geogrid_requests r JOIN geogrid_manual_standard_batches b
                    ON b.batch_id = r.manual_batch_id
                WHERE b.batch_id = %s AND b.operator_id = %s AND r.manual_business_id = %s
                ORDER BY lower(r.keyword), r.created_at, r.request_id
            """, (batch_id, operator, business_id)).fetchall()
    except (LiveFinanceError, psycopg.Error) as error:
        raise HTTPException(503, "Fila financeira indisponível.") from error
    if not rows:
        raise HTTPException(404, "Empresa não encontrada no lote.")
    if len(rows) < 3 or any(row["status"] != "done" or not row["scan_id"] for row in rows):
        raise HTTPException(409, "O consolidado requer pelo menos três palavras-chave concluídas.")
    try:
        scans = [db.get_external_scan("t2s-landing", row["scan_id"]) for row in rows]
    except db.ExternalScanNotFoundError as error:
        raise HTTPException(409, "Pesquisa do lote ainda indisponível.") from error
    return await basic_pdf(scans)


def read_worker_claim(request_id: UUID, claim_token: UUID) -> dict | None:
    """Expose only the parameters of the current unexpired worker claim."""
    with _connect() as connection:
        return connection.execute("""
            SELECT manual_business_id, keyword, grid_size, spacing_km
            FROM geogrid_requests
            WHERE request_id = %s AND claim_token = %s AND claim_expires_at > now()
              AND status = 'claimed' AND manual_batch_id IS NOT NULL
        """, (request_id, claim_token)).fetchone()
