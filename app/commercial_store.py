"""Durable records for the simulated commercial journey."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from . import db


class CommercialConflictError(Exception):
    """An idempotency key was reused with a different or invalid transition."""


class CommercialCapacityError(CommercialConflictError):
    """A configured daily or pending-work limit has been reached."""


def _record(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def get_lead(lead_id: str) -> dict | None:
    with db.connect() as connection:
        return _record(
            connection.execute(
                "SELECT * FROM commercial_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        )


def create_lead(
    payload: dict,
    *,
    payload_hash: str,
    status_token_hash: str,
    confirmation_token_hash: str | None,
) -> tuple[dict, bool]:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM commercial_leads WHERE request_id = ?",
            (payload["request_id"],),
        ).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise CommercialConflictError("lead request ID conflicts with existing data")
            return dict(existing), False
        token_owner = connection.execute(
            "SELECT id FROM commercial_leads WHERE status_token_hash = ?",
            (status_token_hash,),
        ).fetchone()
        if token_owner:
            raise CommercialConflictError("status capability is already associated with another lead")
        lead_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO commercial_leads ("
            "id, request_id, payload_hash, status_token_hash, confirmation_token_hash, "
            "lead_type, business_confirmed, business_search_request_id, business_name, city, "
            "state, address, google_maps_url, contact_name, email, whatsapp, campaign_id, "
            "privacy_acknowledged, privacy_policy_version, marketing_consent, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lead_id,
                payload["request_id"],
                payload_hash,
                status_token_hash,
                confirmation_token_hash,
                payload["lead_type"],
                int(payload["business_confirmed"]),
                payload["search_request_id"],
                payload["business_name"],
                payload["city"],
                payload["state"],
                payload["address"],
                payload["google_maps_url"],
                payload["contact_name"],
                payload["email"],
                payload["whatsapp"],
                payload["campaign_id"],
                1,
                payload["privacy_policy_version"],
                int(payload["marketing_consent"]),
                db.now(),
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_leads WHERE id = ?", (lead_id,)
            ).fetchone()
        ), True


def attach_business(lead_id: str, business_id: int) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE commercial_leads SET business_id = ? "
            "WHERE id = ? AND business_id IS NULL",
            (business_id, lead_id),
        )
        row = connection.execute(
            "SELECT business_id FROM commercial_leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if not row or row["business_id"] != business_id:
            raise CommercialConflictError("lead is already associated with another business")


def get_diagnostic(diagnostic_id: str) -> dict | None:
    with db.connect() as connection:
        return _record(
            connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE id = ?",
                (diagnostic_id,),
            ).fetchone()
        )


def diagnostics_for_lead(lead_id: str) -> list[dict]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT d.*, s.status AS scan_status, s.progress, s.total, s.metrics, "
                "s.error, s.finished, b.name AS business_name "
                "FROM commercial_diagnostics d LEFT JOIN scans s ON s.id = d.scan_id "
                "LEFT JOIN businesses b ON b.id = d.business_id "
                "WHERE d.lead_id = ? ORDER BY d.created_at",
                (lead_id,),
            )
        ]


def get_diagnostic_by_request(request_id: str) -> dict | None:
    with db.connect() as connection:
        return _record(
            connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        )


def _limits() -> dict[str, int]:
    defaults = {
        "daily_diagnostics": 100,
        "daily_points": 2500,
        "pending_diagnostics": 25,
        "pending_points": 500,
    }
    limits = {}
    for key, default in defaults.items():
        variable = f"ZURI_COMMERCIAL_{key.upper()}_LIMIT"
        try:
            limit = int(os.environ.get(variable, str(default)))
        except ValueError as error:
            raise ValueError(f"{variable} must be a positive integer") from error
        if not 1 <= limit <= 100_000:
            raise ValueError(f"{variable} must be between 1 and 100000")
        limits[key] = limit
    return limits


def _check_capacity(connection: sqlite3.Connection, grid_size: int) -> None:
    limits = _limits()
    today = datetime.now(timezone.utc).date().isoformat()
    usage = connection.execute(
        "SELECT COUNT(*) AS diagnostics, COALESCE(SUM(grid_size * grid_size), 0) AS points "
        "FROM commercial_diagnostics WHERE substr(created_at, 1, 10) = ?",
        (today,),
    ).fetchone()
    if (
        usage["diagnostics"] + 1 > limits["daily_diagnostics"]
        or usage["points"] + grid_size * grid_size > limits["daily_points"]
    ):
        raise CommercialCapacityError("configured daily diagnostic capacity is exhausted")
    pending = connection.execute(
        "SELECT COUNT(*) AS diagnostics, COALESCE(SUM(d.grid_size * d.grid_size - s.progress), 0) AS points "
        "FROM commercial_diagnostics d JOIN scans s ON s.id = d.scan_id "
        "WHERE d.status IN ('queued', 'running') AND s.status = 'running'"
    ).fetchone()
    reserved = connection.execute(
        "SELECT COUNT(*) AS diagnostics, COALESCE(SUM(grid_size * grid_size), 0) AS points "
        "FROM commercial_diagnostics WHERE status = 'reserved'"
    ).fetchone()
    if (
        pending["diagnostics"] + reserved["diagnostics"] + 1
        > limits["pending_diagnostics"]
        or pending["points"] + reserved["points"] + grid_size * grid_size
        > limits["pending_points"]
    ):
        raise CommercialCapacityError("configured pending diagnostic capacity is exhausted")


def reserve_diagnostic(
    *,
    diagnostic_id: str,
    request_id: str,
    request_hash: str,
    lead_id: str,
    product_id: str,
    grid_size: int,
    keyword: str,
    confirmed_provider_id: str | None = None,
    confirmed_reference_id: str | None = None,
    confirmation_nonce_hash: str | None = None,
) -> tuple[dict, bool]:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise CommercialConflictError(
                    "diagnostic request ID conflicts with existing data"
                )
            return dict(existing), False
        lead = connection.execute(
            "SELECT business_confirmed FROM commercial_leads WHERE id = ?",
            (lead_id,),
        ).fetchone()
        if not lead or not lead["business_confirmed"]:
            raise CommercialConflictError("diagnostic requires a confirmed business")
        _check_capacity(connection, grid_size)
        if confirmed_provider_id == "google_places":
            if not confirmation_nonce_hash:
                raise CommercialConflictError(
                    "Google Place confirmation nonce is required"
                )
            connection.execute(
                "UPDATE business_finder_confirmations SET request_id = ?, consumed_at = ? "
                "WHERE nonce_hash = ? AND request_id IS NULL AND expires_at >= ?",
                (
                    request_id,
                    db.now(),
                    confirmation_nonce_hash,
                    int(time.time()),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise CommercialConflictError(
                    "business confirmation was expired or already used"
                )
        connection.execute(
            "INSERT INTO commercial_diagnostics ("
            "id, request_id, request_hash, lead_id, product_id, grid_size, keyword, "
            "status, created_at, confirmed_provider_id, confirmed_reference_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?)",
            (
                diagnostic_id,
                request_id,
                request_hash,
                lead_id,
                product_id,
                grid_size,
                keyword,
                db.now(),
                confirmed_provider_id,
                confirmed_reference_id,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE id = ?",
                (diagnostic_id,),
            ).fetchone()
        ), True


def mark_diagnostic_queued(request_id: str) -> dict:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE commercial_diagnostics SET status = 'queued' "
            "WHERE request_id = ? AND status = 'reserved'",
            (request_id,),
        )
        row = connection.execute(
            "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            raise KeyError(request_id)
        if row["status"] not in {"queued", "done", "failed"}:
            raise CommercialConflictError("diagnostic cannot be queued from its current state")
        return dict(row)


def create_diagnostic(
    *,
    request_id: str,
    request_hash: str,
    lead_id: str,
    product_id: str,
    business_id: int,
    scan_id: int,
    grid_size: int,
    keyword: str,
) -> tuple[dict, bool]:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise CommercialConflictError(
                    "diagnostic request ID conflicts with existing data"
                )
            if existing["status"] != "reserved":
                return dict(existing), False
        lead = connection.execute(
            "SELECT business_confirmed, business_id FROM commercial_leads WHERE id = ?",
            (lead_id,),
        ).fetchone()
        if not lead or not lead["business_confirmed"]:
            raise CommercialConflictError("diagnostic requires a confirmed business")
        if lead["business_id"] not in (None, business_id):
            raise CommercialConflictError("diagnostic business does not match the lead")
        reserved = connection.execute(
            "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if (
            not reserved
            or reserved["lead_id"] != lead_id
            or reserved["product_id"] != product_id
            or reserved["grid_size"] != grid_size
            or reserved["keyword"] != keyword
            or reserved["status"] != "reserved"
        ):
            raise CommercialConflictError("diagnostic reservation does not match")
        connection.execute(
            "UPDATE commercial_diagnostics SET business_id = ?, scan_id = ?, status = 'queued' "
            "WHERE request_id = ?",
            (business_id, scan_id, request_id),
        )
        connection.execute(
            "UPDATE commercial_leads SET business_id = ? WHERE id = ?",
            (business_id, lead_id),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        ), True


def create_order(
    *,
    order_id: str,
    request_id: str,
    request_hash: str,
    lead_id: str,
    source_diagnostic_id: str,
    product_id: str,
    amount_brl: str,
    assisted: bool = False,
) -> tuple[dict, bool]:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM commercial_orders WHERE request_id = ?", (request_id,)
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise CommercialConflictError("order request ID conflicts with existing data")
            return dict(existing), False
        diagnostic = connection.execute(
            "SELECT d.lead_id, d.product_id, s.status AS scan_status "
            "FROM commercial_diagnostics d JOIN scans s ON s.id = d.scan_id "
            "WHERE d.id = ?",
            (source_diagnostic_id,),
        ).fetchone()
        if (
            not diagnostic
            or diagnostic["lead_id"] != lead_id
            or diagnostic["scan_status"] != "done"
            or diagnostic["product_id"] != "express"
        ):
            raise CommercialConflictError("order requires a completed Express diagnostic for this lead")
        status = (
            "assisted"
            if assisted or product_id == "monitor"
            else "awaiting_payment_confirmation"
        )
        connection.execute(
            "INSERT INTO commercial_orders ("
            "id, request_id, request_hash, lead_id, source_diagnostic_id, product_id, "
            "amount_brl, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id,
                request_id,
                request_hash,
                lead_id,
                source_diagnostic_id,
                product_id,
                amount_brl,
                status,
                db.now(),
            ),
        )
        connection.execute(
            "INSERT INTO commercial_order_events "
            "(order_id, actor_id, event_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                order_id,
                "customer",
                "created",
                json.dumps(
                    {
                        "product_id": product_id,
                        "fulfillment": "assisted" if status == "assisted" else "payment",
                    }
                ),
                db.now(),
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_orders WHERE id = ?", (order_id,)
            ).fetchone()
        ), True


def get_order(order_id: str) -> dict | None:
    with db.connect() as connection:
        return _record(
            connection.execute(
                "SELECT * FROM commercial_orders WHERE id = ?", (order_id,)
            ).fetchone()
        )


def list_orders_for_lead(lead_id: str) -> list[dict]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM commercial_orders WHERE lead_id = ? ORDER BY created_at",
                (lead_id,),
            )
        ]


def order_events(order_id: str) -> list[dict]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT actor_id, event_type, details, created_at "
                "FROM commercial_order_events WHERE order_id = ? ORDER BY id",
                (order_id,),
            )
        ]


def confirm_order_payment(
    order_id: str,
    *,
    payment_reference: str,
    operator_id: str,
) -> dict:
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute(
            "SELECT * FROM commercial_orders WHERE id = ?", (order_id,)
        ).fetchone()
        if not order:
            raise KeyError(order_id)
        if order["status"] in {"payment_confirmed", "released"}:
            if (
                order["payment_reference"] == payment_reference
                and order["operator_id"] == operator_id
            ):
                return dict(order)
            raise CommercialConflictError("payment was already confirmed with different details")
        if order["status"] != "awaiting_payment_confirmation":
            raise CommercialConflictError("order is not awaiting payment confirmation")
        confirmed_at = db.now()
        connection.execute(
            "UPDATE commercial_orders SET status = 'payment_confirmed', "
            "payment_reference = ?, operator_id = ?, payment_confirmed_at = ? WHERE id = ?",
            (payment_reference, operator_id, confirmed_at, order_id),
        )
        connection.execute(
            "INSERT INTO commercial_order_events "
            "(order_id, actor_id, event_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                order_id,
                operator_id,
                "payment_confirmed",
                json.dumps({"payment_reference_recorded": True}),
                confirmed_at,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_orders WHERE id = ?", (order_id,)
            ).fetchone()
        )


def release_order(
    order_id: str,
    *,
    operator_id: str,
    grid_size: int = 7,
) -> tuple[dict, bool]:
    """Atomically create the Professional scan and authorize it exactly once."""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute(
            "SELECT * FROM commercial_orders WHERE id = ?", (order_id,)
        ).fetchone()
        if not order:
            raise KeyError(order_id)
        if order["status"] == "released":
            diagnostic = connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE id = ?",
                (order["diagnostic_id"],),
            ).fetchone()
            return dict(diagnostic), False
        if order["product_id"] != "professional" or order["status"] != "payment_confirmed":
            raise CommercialConflictError("only a payment-confirmed Professional order can be released")
        source = connection.execute(
            "SELECT d.keyword, d.business_id, b.lat, b.lng, b.name "
            "FROM commercial_diagnostics d JOIN businesses b ON b.id = d.business_id "
            "WHERE d.id = ? AND d.lead_id = ?",
            (order["source_diagnostic_id"], order["lead_id"]),
        ).fetchone()
        if not source:
            raise CommercialConflictError("Professional order has no valid source diagnostic")
        if grid_size not in {3, 7}:
            raise CommercialConflictError("unsupported Professional grid size")
        _check_capacity(connection, grid_size)
        scan_cursor = connection.execute(
            "INSERT INTO scans (business_id, keyword, grid_size, spacing_km, center_lat, "
            "center_lng, total, created, origin) VALUES (?, ?, ?, 1.0, ?, ?, ?, ?, 'commercial')",
            (
                source["business_id"],
                source["keyword"],
                grid_size,
                source["lat"],
                source["lng"],
                grid_size * grid_size,
                db.now(),
            ),
        )
        scan_id = scan_cursor.lastrowid
        diagnostic_id = str(uuid.uuid4())
        request_id = f"order-release:{order_id}"
        request_hash = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO commercial_diagnostics ("
            "id, request_id, request_hash, lead_id, product_id, business_id, scan_id, "
            "grid_size, keyword, status, created_at"
            ") VALUES (?, ?, ?, ?, 'professional', ?, ?, ?, ?, 'queued', ?)",
            (
                diagnostic_id,
                request_id,
                request_hash,
                order["lead_id"],
                source["business_id"],
                scan_id,
                grid_size,
                source["keyword"],
                db.now(),
            ),
        )
        released_at = db.now()
        connection.execute(
            "UPDATE commercial_orders SET status = 'released', diagnostic_id = ?, "
            "operator_id = ?, released_at = ? WHERE id = ?",
            (diagnostic_id, operator_id, released_at, order_id),
        )
        connection.execute(
            "INSERT INTO commercial_order_events "
            "(order_id, actor_id, event_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                order_id,
                operator_id,
                "diagnostic_released",
                json.dumps({"diagnostic_id": diagnostic_id, "grid_size": grid_size}),
                released_at,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM commercial_diagnostics WHERE id = ?", (diagnostic_id,)
            ).fetchone()
        ), True
