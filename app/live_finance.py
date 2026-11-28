"""Financial authorization and reconciliation for manual DataForSEO Live scans."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Protocol, runtime_checkable

import psycopg
from psycopg.rows import dict_row

from .fetchers.base import FetchStatusError
from .grid import GridPoint

log = logging.getLogger("geogrid.live_finance")

_ADVISORY_LOCK = (1847219, 1)
_MONEY_QUANTUM = Decimal("0.000001")
_MAX_IN_FLIGHT = 2
_EVENT_KEY = "manual-live"


class LiveFinanceError(Exception):
    """The financial ledger could not safely authorize or account for a scan."""


class LiveFinanceUnavailable(LiveFinanceError):
    """The PostgreSQL financial ledger is unavailable or misconfigured."""


class LiveBudgetDenied(LiveFinanceError):
    """The current authorization or financial limits do not permit the scan."""


class LiveIdempotencyConflict(LiveFinanceError):
    """An idempotency key was reused with a different scan payload."""


@dataclass(frozen=True)
class LiveReservation:
    idempotency_key: uuid.UUID
    claim_token: uuid.UUID | None
    status: str
    scan_ids: list[int] | None
    reserved_usd: Decimal
    created: bool


@runtime_checkable
class CostAwareFetcher(Protocol):
    async def fetch_with_cost(
        self, keyword: str, lat: float, lng: float
    ) -> tuple[list[dict], Decimal | None]: ...


def scan_fingerprint(
    *,
    operator_id: str,
    business_id: int,
    keywords: list[str],
    grid_size: int,
    spacing_km: float,
    center: tuple[float, float],
) -> str:
    """Hash all parameters whose change must prevent idempotent resubmission."""

    canonical = json.dumps(
        {
            "operator_id": operator_id,
            "business_id": business_id,
            "keywords": keywords,
            "grid_size": grid_size,
            "spacing_km": spacing_km,
            "center": center,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(value: object, *, allow_zero: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("invalid monetary amount") from error
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        raise ValueError("invalid monetary amount")
    return amount.quantize(_MONEY_QUANTUM, rounding=ROUND_CEILING)


def _connect():
    dsn = os.environ.get("GEOGRID_FINANCE_DATABASE_URL", "").strip()
    if not dsn:
        raise LiveFinanceUnavailable("PostgreSQL finance connection is not configured")
    try:
        return psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row)
    except psycopg.Error as error:
        raise LiveFinanceUnavailable("PostgreSQL finance ledger is unavailable") from error


def _translate_database_error(error: psycopg.Error) -> LiveFinanceUnavailable:
    return LiveFinanceUnavailable("PostgreSQL finance ledger operation failed")


def _lock_shared_ledger(connection) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        _ADVISORY_LOCK,
    )


def _lock_budget_rows(connection, daily_key: str, event_key: str) -> dict:
    rows = connection.execute(
        """
        SELECT scope_type, scope_key, cap_amount_usd,
               spent_amount_usd, reserved_amount_usd
        FROM geogrid_budget
        WHERE (scope_type, scope_key) IN (
            ('daily', %s), ('event', %s)
        )
        ORDER BY scope_type, scope_key
        FOR UPDATE
        """,
        (daily_key, event_key),
    ).fetchall()
    return {(row["scope_type"], row["scope_key"]): row for row in rows}


def _update_budget_amounts(
    connection,
    daily_key: str,
    event_key: str,
    *,
    reserve_delta: Decimal = Decimal("0"),
    spent_delta: Decimal = Decimal("0"),
) -> None:
    cursor = connection.execute(
        """
        UPDATE geogrid_budget
        SET reserved_amount_usd = reserved_amount_usd + %s,
            spent_amount_usd = spent_amount_usd + %s,
            updated_at = now()
        WHERE (scope_type, scope_key) IN (
            ('daily', %s), ('event', %s)
        )
          AND reserved_amount_usd + %s >= 0
        """,
        (
            reserve_delta,
            spent_delta,
            daily_key,
            event_key,
            reserve_delta,
        ),
    )
    if cursor.rowcount != 2:
        raise LiveFinanceUnavailable("Shared budget ledger counters are inconsistent")


def reserve_live_scan(
    *,
    idempotency_key: uuid.UUID,
    request_fingerprint: str,
    operator_id: str,
    business_id: int,
    grid_size: int,
    keyword_count: int,
) -> LiveReservation:
    total_queries = grid_size * grid_size * keyword_count
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            existing = connection.execute(
                """
                SELECT request_fingerprint, claim_token, status, scan_ids,
                       reserved_usd
                FROM geogrid_live_scans
                WHERE idempotency_key = %s
                FOR UPDATE
                """,
                (idempotency_key,),
            ).fetchone()
            if existing:
                if not hmac.compare_digest(
                    existing["request_fingerprint"], request_fingerprint
                ):
                    raise LiveIdempotencyConflict(
                        "Idempotency key was already used for a different scan"
                    )
                return LiveReservation(
                    idempotency_key=idempotency_key,
                    claim_token=None,
                    status=existing["status"],
                    scan_ids=list(existing["scan_ids"]) if existing["scan_ids"] else None,
                    reserved_usd=existing["reserved_usd"],
                    created=False,
                )

            control = connection.execute(
                """
                SELECT enabled, daily_cap_usd, default_event_cap_usd,
                       estimated_live_task_cost_usd
                FROM geogrid_budget
                WHERE scope_type = 'system' AND scope_key = 'default'
                FOR UPDATE
                """
            ).fetchone()
            if (
                not control
                or not control["enabled"]
                or control["daily_cap_usd"] is None
                or control["default_event_cap_usd"] is None
                or control["estimated_live_task_cost_usd"] is None
                or control["estimated_live_task_cost_usd"] <= 0
            ):
                raise LiveBudgetDenied(
                    "Live pricing or global financial controls are not configured"
                )

            unit_cost = control["estimated_live_task_cost_usd"]
            reserved = _decimal(unit_cost * total_queries, allow_zero=False)
            daily_key = datetime.now(timezone.utc).date().isoformat()
            event_key = _EVENT_KEY

            connection.execute(
                """
                INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
                VALUES ('daily', %s, %s)
                ON CONFLICT (scope_type, scope_key) DO NOTHING
                """,
                (daily_key, control["daily_cap_usd"]),
            )
            connection.execute(
                """
                INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
                VALUES ('event', %s, %s)
                ON CONFLICT (scope_type, scope_key) DO NOTHING
                """,
                (event_key, control["default_event_cap_usd"]),
            )
            budgets = _lock_budget_rows(connection, daily_key, event_key)
            daily = budgets.get(("daily", daily_key))
            event = budgets.get(("event", event_key))
            if (
                not daily
                or not event
                or daily["cap_amount_usd"] is None
                or event["cap_amount_usd"] is None
                or daily["spent_amount_usd"]
                + daily["reserved_amount_usd"]
                + reserved
                > daily["cap_amount_usd"]
                or event["spent_amount_usd"]
                + event["reserved_amount_usd"]
                + reserved
                > event["cap_amount_usd"]
            ):
                raise LiveBudgetDenied(
                    "Daily or operation budget is insufficient for this scan"
                )

            claim_token = uuid.uuid4()
            connection.execute(
                """
                INSERT INTO geogrid_live_scans (
                    idempotency_key, request_fingerprint, operator_id,
                    business_id, grid_size, keyword_count, total_queries,
                    unit_cost_usd, reserved_usd, remaining_reserved_usd,
                    budget_daily_key, budget_event_key, claim_token, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'reserved')
                """,
                (
                    idempotency_key,
                    request_fingerprint,
                    operator_id,
                    business_id,
                    grid_size,
                    keyword_count,
                    total_queries,
                    unit_cost,
                    reserved,
                    reserved,
                    daily_key,
                    event_key,
                    claim_token,
                ),
            )
            _update_budget_amounts(
                connection,
                daily_key,
                event_key,
                reserve_delta=reserved,
            )
            return LiveReservation(
                idempotency_key=idempotency_key,
                claim_token=claim_token,
                status="reserved",
                scan_ids=None,
                reserved_usd=reserved,
                created=True,
            )
    except (LiveFinanceError, LiveBudgetDenied, LiveIdempotencyConflict):
        raise
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def attach_live_scan_ids(
    idempotency_key: uuid.UUID,
    claim_token: uuid.UUID,
    scan_ids: list[int],
) -> bool:
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            request = connection.execute(
                """
                SELECT claim_token, keyword_count, scan_ids, status
                FROM geogrid_live_scans
                WHERE idempotency_key = %s
                FOR UPDATE
                """,
                (idempotency_key,),
            ).fetchone()
            if (
                not request
                or request["claim_token"] != claim_token
                or len(scan_ids) != request["keyword_count"]
            ):
                return False
            if request["scan_ids"] is not None:
                return (
                    list(request["scan_ids"]) == scan_ids
                    and request["status"] in {
                        "queued", "running", "completed", "uncertain", "cost_overrun"
                    }
                )
            if request["status"] != "reserved":
                return False
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET scan_ids = %s, status = 'queued', updated_at = now()
                WHERE idempotency_key = %s
                """,
                (scan_ids, idempotency_key),
            )
            return True
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def _get_live_request(connection, idempotency_key: uuid.UUID):
    return connection.execute(
        """
        SELECT * FROM geogrid_live_scans
        WHERE idempotency_key = %s
        FOR UPDATE
        """,
        (idempotency_key,),
    ).fetchone()


def authorize_live_query(
    *,
    idempotency_key: uuid.UUID,
    claim_token: uuid.UUID,
    operator_id: str,
    scan_index: int,
    scan_id: int,
    point_index: int,
) -> bool:
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            control = connection.execute(
                """
                SELECT enabled, estimated_live_task_cost_usd
                FROM geogrid_budget
                WHERE scope_type = 'system' AND scope_key = 'default'
                FOR UPDATE
                """
            ).fetchone()
            request = _get_live_request(connection, idempotency_key)
            if (
                not control
                or not control["enabled"]
                or not request
                or request["claim_token"] != claim_token
                or request["operator_id"] != operator_id
                or request["status"] not in {"queued", "running"}
                or request["scan_ids"] is None
                or scan_index < 0
                or scan_index >= request["keyword_count"]
                or request["scan_ids"][scan_index] != scan_id
                or point_index < 0
                or point_index >= request["grid_size"] * request["grid_size"]
                or control["estimated_live_task_cost_usd"] != request["unit_cost_usd"]
                or request["remaining_reserved_usd"] < request["unit_cost_usd"]
            ):
                return False

            budgets = _lock_budget_rows(
                connection,
                request["budget_daily_key"],
                request["budget_event_key"],
            )
            daily = budgets.get(("daily", request["budget_daily_key"]))
            event = budgets.get(("event", request["budget_event_key"]))
            if (
                not daily
                or not event
                or daily["cap_amount_usd"] is None
                or event["cap_amount_usd"] is None
                or daily["spent_amount_usd"] + daily["reserved_amount_usd"]
                > daily["cap_amount_usd"]
                or event["spent_amount_usd"] + event["reserved_amount_usd"]
                > event["cap_amount_usd"]
            ):
                return False

            pending = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s
                  AND state IN ('authorized', 'uncertain')
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            if pending >= _MAX_IN_FLIGHT:
                return False
            connection.execute(
                """
                INSERT INTO geogrid_live_scan_queries (
                    idempotency_key, scan_index, point_index, state
                )
                VALUES (%s, %s, %s, 'authorized')
                """,
                (idempotency_key, scan_index, point_index),
            )
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET status = 'running', updated_at = now()
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            )
            return True
    except psycopg.errors.UniqueViolation:
        return False
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def record_live_query_cost(
    *,
    idempotency_key: uuid.UUID,
    scan_index: int,
    point_index: int,
    actual_cost_usd: Decimal,
) -> bool:
    cost = _decimal(actual_cost_usd)
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            request = _get_live_request(connection, idempotency_key)
            query = connection.execute(
                """
                SELECT state, actual_cost_usd
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND scan_index = %s AND point_index = %s
                FOR UPDATE
                """,
                (idempotency_key, scan_index, point_index),
            ).fetchone()
            if not request or not query:
                return False
            if query["state"] == "completed":
                if query["actual_cost_usd"] == cost:
                    return True
                raise LiveFinanceUnavailable(
                    "Conflicting provider costs were returned for one query"
                )
            if query["state"] != "authorized":
                return False

            unit_cost = request["unit_cost_usd"]
            if request["remaining_reserved_usd"] < unit_cost:
                raise LiveFinanceUnavailable("Live scan reservation is inconsistent")
            budgets = _lock_budget_rows(
                connection,
                request["budget_daily_key"],
                request["budget_event_key"],
            )
            if not all(
                budgets.get(key)
                for key in (
                    ("daily", request["budget_daily_key"]),
                    ("event", request["budget_event_key"]),
                )
            ):
                raise LiveFinanceUnavailable("Live scan budget scopes are missing")
            _update_budget_amounts(
                connection,
                request["budget_daily_key"],
                request["budget_event_key"],
                reserve_delta=-unit_cost,
                spent_delta=cost,
            )
            connection.execute(
                """
                UPDATE geogrid_live_scan_queries
                SET state = 'completed', actual_cost_usd = %s,
                    completed_at = now()
                WHERE idempotency_key = %s AND scan_index = %s AND point_index = %s
                """,
                (cost, idempotency_key, scan_index, point_index),
            )
            remaining = request["remaining_reserved_usd"] - unit_cost
            total_cost = request["actual_cost_usd"] + cost
            completed = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND state = 'completed'
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            uncertain = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND state = 'uncertain'
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            if cost > unit_cost:
                status = "cost_overrun"
            elif completed == request["total_queries"] and uncertain == 0:
                status = "completed"
            else:
                status = request["status"]
                if status in {"queued", "reserved"}:
                    status = "running"
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET remaining_reserved_usd = %s, actual_cost_usd = %s,
                    status = %s, updated_at = now(),
                    finished_at = CASE WHEN %s = 'completed' THEN now() ELSE finished_at END
                WHERE idempotency_key = %s
                """,
                (remaining, total_cost, status, status, idempotency_key),
            )
            return True
    except (LiveFinanceError, LiveBudgetDenied, LiveIdempotencyConflict):
        raise
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def mark_live_query_uncertain(
    idempotency_key: uuid.UUID,
    scan_index: int,
    point_index: int,
) -> bool:
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            request = _get_live_request(connection, idempotency_key)
            query = connection.execute(
                """
                SELECT state FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND scan_index = %s AND point_index = %s
                FOR UPDATE
                """,
                (idempotency_key, scan_index, point_index),
            ).fetchone()
            if not request or not query:
                return False
            if query["state"] == "uncertain":
                return True
            if query["state"] != "authorized":
                return False
            connection.execute(
                """
                UPDATE geogrid_live_scan_queries
                SET state = 'uncertain'
                WHERE idempotency_key = %s AND scan_index = %s AND point_index = %s
                """,
                (idempotency_key, scan_index, point_index),
            )
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET status = 'uncertain', updated_at = now()
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            )
            return True
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def finish_live_scan(idempotency_key: uuid.UUID) -> str:
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            request = _get_live_request(connection, idempotency_key)
            if not request:
                raise LiveFinanceUnavailable("Live scan reservation does not exist")
            if request["status"] in {"completed", "failed", "released"}:
                return request["status"]
            pending = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s
                  AND state IN ('authorized', 'uncertain')
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            if pending:
                if request["status"] != "cost_overrun":
                    connection.execute(
                        """
                        UPDATE geogrid_live_scans
                        SET status = 'uncertain', updated_at = now()
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
                    return "uncertain"
                return "cost_overrun"
            if request["status"] == "cost_overrun":
                return "cost_overrun"

            remaining = request["remaining_reserved_usd"]
            if remaining:
                budgets = _lock_budget_rows(
                    connection,
                    request["budget_daily_key"],
                    request["budget_event_key"],
                )
                if not all(
                    budgets.get(key)
                    for key in (
                        ("daily", request["budget_daily_key"]),
                        ("event", request["budget_event_key"]),
                    )
                ):
                    raise LiveFinanceUnavailable("Live scan budget scopes are missing")
                _update_budget_amounts(
                    connection,
                    request["budget_daily_key"],
                    request["budget_event_key"],
                    reserve_delta=-remaining,
                )
            completed = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND state = 'completed'
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            status = "completed" if completed == request["total_queries"] else "failed"
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET remaining_reserved_usd = 0, status = %s,
                    updated_at = now(), finished_at = now()
                WHERE idempotency_key = %s
                """,
                (status, idempotency_key),
            )
            return status
    except LiveFinanceError:
        raise
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


def reconcile_live_scan(
    idempotency_key: uuid.UUID,
    *,
    total_cost_usd: Decimal,
    evidence_reference: str,
) -> None:
    """Reconcile a provider-confirmed total; invoke only after manual verification."""

    cost = _decimal(total_cost_usd)
    evidence = evidence_reference.strip()
    if not evidence or len(evidence) > 512:
        raise ValueError("a bounded reconciliation evidence reference is required")
    try:
        with _connect() as connection:
            _lock_shared_ledger(connection)
            request = _get_live_request(connection, idempotency_key)
            if (
                not request
                or request["status"] not in {"uncertain", "cost_overrun"}
            ):
                raise LiveBudgetDenied("Live scan is not awaiting reconciliation")
            in_flight = connection.execute(
                """
                SELECT count(*) AS count
                FROM geogrid_live_scan_queries
                WHERE idempotency_key = %s AND state = 'authorized'
                """,
                (idempotency_key,),
            ).fetchone()["count"]
            if in_flight:
                raise LiveBudgetDenied("Live scan still has in-flight provider queries")
            if cost < request["actual_cost_usd"]:
                raise LiveBudgetDenied(
                    "Reconciled total is below costs already recorded"
                )

            delta = cost - request["actual_cost_usd"]
            remaining = request["remaining_reserved_usd"]
            budgets = _lock_budget_rows(
                connection,
                request["budget_daily_key"],
                request["budget_event_key"],
            )
            if not all(
                budgets.get(key)
                for key in (
                    ("daily", request["budget_daily_key"]),
                    ("event", request["budget_event_key"]),
                )
            ):
                raise LiveFinanceUnavailable("Live scan budget scopes are missing")
            _update_budget_amounts(
                connection,
                request["budget_daily_key"],
                request["budget_event_key"],
                reserve_delta=-remaining,
                spent_delta=delta,
            )
            connection.execute(
                """
                UPDATE geogrid_live_scan_queries
                SET state = 'reconciled', completed_at = now()
                WHERE idempotency_key = %s AND state = 'uncertain'
                """,
                (idempotency_key,),
            )
            connection.execute(
                """
                UPDATE geogrid_live_scans
                SET actual_cost_usd = %s, remaining_reserved_usd = 0,
                    status = 'completed', reconciliation_reference = %s,
                    updated_at = now(), finished_at = now()
                WHERE idempotency_key = %s
                """,
                (cost, evidence, idempotency_key),
            )
    except (LiveFinanceError, LiveBudgetDenied):
        raise
    except psycopg.Error as error:
        raise _translate_database_error(error) from error


@dataclass(frozen=True)
class LiveScanExecutor:
    """One reserved, idempotent manual scan batch."""

    idempotency_key: uuid.UUID
    claim_token: uuid.UUID
    operator_id: str
    scan_ids: list[int]

    async def fetch_point(
        self,
        fetcher: object,
        *,
        scan_index: int,
        scan_id: int,
        point: GridPoint,
        grid_size: int,
        keyword: str,
    ) -> list[dict]:
        if not isinstance(fetcher, CostAwareFetcher):
            raise FetchStatusError(
                "The configured Live provider does not expose a verifiable task cost",
                category="budget_limit",
            )
        point_index = point.row * grid_size + point.col
        try:
            authorized = await asyncio.to_thread(
                authorize_live_query,
                idempotency_key=self.idempotency_key,
                claim_token=self.claim_token,
                operator_id=self.operator_id,
                scan_index=scan_index,
                scan_id=scan_id,
                point_index=point_index,
            )
        except LiveFinanceError as error:
            raise FetchStatusError(
                "Financial authorization could not be revalidated",
                category="budget_limit",
            ) from error
        if not authorized:
            raise FetchStatusError(
                "Financial authorization or remaining budget was denied",
                category="budget_limit",
            )

        try:
            results, actual_cost = await fetcher.fetch_with_cost(
                keyword, point.lat, point.lng
            )
        except FetchStatusError as error:
            if error.cost_usd is None:
                await self._mark_uncertain(scan_index, point_index, error)
                raise FetchStatusError(
                    "DataForSEO outcome is uncertain; the reservation is held for reconciliation",
                    category="submission_uncertain",
                    api_status_code=error.api_status_code,
                    api_status_message=error.api_status_message,
                ) from error
            else:
                await self._record_cost(scan_index, point_index, error.cost_usd)
            raise
        except Exception as error:
            await self._mark_uncertain(scan_index, point_index, error)
            raise FetchStatusError(
                "DataForSEO outcome is uncertain; the reservation is held for reconciliation",
                category="submission_uncertain",
            ) from error

        if actual_cost is None:
            error = FetchStatusError(
                "DataForSEO did not return a verifiable task cost",
                category="submission_uncertain",
            )
            await self._mark_uncertain(scan_index, point_index, error)
            raise error
        await self._record_cost(scan_index, point_index, actual_cost)
        return results

    async def _record_cost(
        self,
        scan_index: int,
        point_index: int,
        actual_cost: Decimal,
    ) -> None:
        try:
            recorded = await asyncio.to_thread(
                record_live_query_cost,
                idempotency_key=self.idempotency_key,
                scan_index=scan_index,
                point_index=point_index,
                actual_cost_usd=actual_cost,
            )
            if not recorded:
                raise LiveFinanceUnavailable("Provider cost could not be committed")
        except LiveFinanceError as error:
            await self._mark_uncertain(scan_index, point_index, error)
            raise FetchStatusError(
                "Provider outcome was received but its cost could not be reconciled; "
                "the reservation is held",
                category="submission_uncertain",
            ) from error

    async def _mark_uncertain(
        self,
        scan_index: int,
        point_index: int,
        cause: Exception,
    ) -> None:
        try:
            marked = await asyncio.to_thread(
                mark_live_query_uncertain,
                self.idempotency_key,
                scan_index,
                point_index,
            )
        except LiveFinanceError as error:
            raise FetchStatusError(
                "Provider outcome is uncertain and the reserved budget remains held",
                category="submission_uncertain",
            ) from error
        if not marked:
            log.error(
                "live query uncertainty could not be recorded for request %s after %s",
                self.idempotency_key,
                type(cause).__name__,
            )

    async def finish(self) -> None:
        try:
            await asyncio.to_thread(finish_live_scan, self.idempotency_key)
        except LiveFinanceError:
            log.exception(
                "live scan finalization failed; reservation remains held"
            )
