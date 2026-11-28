"""SQLite storage (stdlib). One short-lived connection per operation."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("GEOGRID_DB", Path(__file__).resolve().parent.parent / "geogrid.db"))


class ExternalConflictError(Exception):
    """The external request conflicts with an existing tenant-scoped record."""


class ExternalScanNotFoundError(Exception):
    """The external scan does not exist for the authenticated client."""


class ExternalInvalidGridError(Exception):
    """The registered business center cannot produce valid grid coordinates."""


class ExternalPendingPointsError(Exception):
    def __init__(self, pending_points: int):
        self.pending_points = pending_points


SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    cid TEXT UNIQUE,
    place_id TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    address TEXT,
    category TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    keyword TEXT NOT NULL,
    grid_size INTEGER NOT NULL,
    spacing_km REAL NOT NULL,
    center_lat REAL NOT NULL,
    center_lng REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    metrics TEXT,
    competitors TEXT,
    error TEXT,
    created TEXT NOT NULL,
    finished TEXT,
    origin TEXT NOT NULL DEFAULT 'web',
    result_provider TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    row INTEGER NOT NULL,
    col INTEGER NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    rank INTEGER,                              -- NULL = not in top 20 for a valid result
    status TEXT NOT NULL,                      -- ok | no_results | error | pending
    error TEXT,
    results TEXT,
    error_category TEXT,
    api_status_code INTEGER,
    api_status_message TEXT,
    external_task_id TEXT
);
CREATE TABLE IF NOT EXISTS external_businesses (
    client_id TEXT NOT NULL,
    external_business_id TEXT NOT NULL,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    created TEXT NOT NULL,
    PRIMARY KEY (client_id, external_business_id)
);
CREATE TABLE IF NOT EXISTS external_scans (
    external_id TEXT PRIMARY KEY,
    scan_id INTEGER NOT NULL UNIQUE REFERENCES scans(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    external_request_id TEXT NOT NULL,
    external_business_id TEXT NOT NULL,
    language_code TEXT NOT NULL DEFAULT 'pt',
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'partial', 'done', 'failed')),
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    finished TEXT,
    completion_mode TEXT,
    completion_reason TEXT,
    commercial_diagnostic_id TEXT REFERENCES commercial_diagnostics(id),
    UNIQUE (client_id, external_request_id)
);
CREATE INDEX IF NOT EXISTS idx_points_scan ON points(scan_id);
CREATE INDEX IF NOT EXISTS idx_scans_business ON scans(business_id, keyword);
CREATE INDEX IF NOT EXISTS idx_external_business_client ON external_businesses(client_id, external_business_id);
CREATE INDEX IF NOT EXISTS idx_external_scan_client ON external_scans(client_id, external_id);
CREATE TABLE IF NOT EXISTS business_finder_daily_usage (
    usage_date TEXT PRIMARY KEY,
    query_count INTEGER NOT NULL CHECK (query_count >= 0)
);
CREATE TABLE IF NOT EXISTS business_finder_rate_buckets (
    subject_hash TEXT NOT NULL,
    bucket_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL CHECK (request_count >= 0),
    PRIMARY KEY (subject_hash, bucket_start)
);
CREATE TABLE IF NOT EXISTS business_finder_confirmations (
    nonce_hash TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL,
    request_id TEXT,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_finder_confirmation_requests (
    request_id TEXT PRIMARY KEY,
    input_hash TEXT NOT NULL,
    confirmation_nonce_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_finder_searches (
    request_id TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'uncertain')),
    classification TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_finder_data_provenance (
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    field_name TEXT NOT NULL,
    data_origin TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    license_name TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    valid_until TEXT,
    storage_authorized INTEGER NOT NULL CHECK (storage_authorized IN (0, 1)),
    attribution TEXT,
    PRIMARY KEY (business_id, field_name, data_origin, provider_id)
);
CREATE TABLE IF NOT EXISTS business_finder_provider_refs (
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    provider_id TEXT NOT NULL,
    provider_reference_id TEXT NOT NULL,
    license_name TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    valid_until TEXT,
    storage_authorized INTEGER NOT NULL CHECK (storage_authorized IN (0, 1)),
    attribution TEXT,
    PRIMARY KEY (business_id, provider_id, provider_reference_id)
);
CREATE TABLE IF NOT EXISTS business_finder_diagnostics (
    request_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    scan_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commercial_leads (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    status_token_hash TEXT NOT NULL UNIQUE,
    confirmation_token_hash TEXT,
    lead_type TEXT NOT NULL CHECK (lead_type IN ('confirmed', 'assisted')),
    business_confirmed INTEGER NOT NULL CHECK (business_confirmed IN (0, 1)),
    business_search_request_id TEXT,
    business_id INTEGER REFERENCES businesses(id),
    business_name TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    address TEXT,
    google_maps_url TEXT,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    whatsapp TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    privacy_acknowledged INTEGER NOT NULL CHECK (privacy_acknowledged IN (0, 1)),
    privacy_policy_version TEXT NOT NULL,
    marketing_consent INTEGER NOT NULL CHECK (marketing_consent IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commercial_diagnostics (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    lead_id TEXT NOT NULL REFERENCES commercial_leads(id),
    product_id TEXT NOT NULL CHECK (product_id IN ('express', 'professional')),
    business_id INTEGER REFERENCES businesses(id),
    scan_id INTEGER UNIQUE REFERENCES scans(id),
    grid_size INTEGER NOT NULL CHECK (grid_size IN (3, 7)),
    keyword TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'queued', 'running', 'done', 'failed')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commercial_orders (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    lead_id TEXT NOT NULL REFERENCES commercial_leads(id),
    source_diagnostic_id TEXT NOT NULL REFERENCES commercial_diagnostics(id),
    product_id TEXT NOT NULL CHECK (product_id IN ('professional', 'monitor')),
    amount_brl TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('awaiting_payment_confirmation', 'payment_confirmed', 'released', 'assisted')
    ),
    payment_reference TEXT,
    operator_id TEXT,
    diagnostic_id TEXT REFERENCES commercial_diagnostics(id),
    created_at TEXT NOT NULL,
    payment_confirmed_at TEXT,
    released_at TEXT
);
CREATE TABLE IF NOT EXISTS commercial_order_events (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES commercial_orders(id),
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commercial_diagnostics_lead
    ON commercial_diagnostics(lead_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_commercial_leads_one_express
    ON commercial_diagnostics(lead_id) WHERE product_id = 'express';
CREATE INDEX IF NOT EXISTS idx_commercial_orders_lead
    ON commercial_orders(lead_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_commercial_orders_one_product_per_lead
    ON commercial_orders(lead_id, product_id);
CREATE INDEX IF NOT EXISTS idx_commercial_order_events_order
    ON commercial_order_events(order_id, id);
CREATE TABLE IF NOT EXISTS commercial_prospects (
    id INTEGER PRIMARY KEY,
    identity_hash TEXT NOT NULL UNIQUE,
    identity_type TEXT NOT NULL CHECK (
        identity_type IN ('cid', 'place_id', 'name_address', 'name_location')
    ),
    source_provider TEXT NOT NULL,
    source_license TEXT NOT NULL,
    data_mode TEXT NOT NULL CHECK (data_mode IN ('synthetic', 'authorized')),
    storage_authorized INTEGER NOT NULL CHECK (storage_authorized IN (0, 1)),
    export_eligible INTEGER NOT NULL CHECK (export_eligible IN (0, 1)),
    name TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    cid TEXT,
    place_id TEXT,
    address TEXT,
    category TEXT,
    lat REAL,
    lng REAL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    best_rank INTEGER,
    qualification TEXT NOT NULL CHECK (
        qualification IN ('priority', 'standard', 'low', 'simulation')
    ),
    crm_sync_status TEXT NOT NULL DEFAULT 'not_configured'
);
CREATE TABLE IF NOT EXISTS commercial_prospect_observations (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES commercial_prospects(id),
    lead_id TEXT REFERENCES commercial_leads(id),
    diagnostic_id TEXT REFERENCES commercial_diagnostics(id),
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    keyword TEXT NOT NULL,
    point_row INTEGER NOT NULL,
    point_col INTEGER NOT NULL,
    result_rank INTEGER NOT NULL,
    provider_position INTEGER,
    record_status TEXT NOT NULL DEFAULT 'previously_known' CHECK (
        record_status IN ('new', 'previously_known')
    ),
    source_provider TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE (prospect_id, scan_id, point_row, point_col)
);
CREATE INDEX IF NOT EXISTS idx_commercial_prospect_seen
    ON commercial_prospects(last_seen_at DESC, qualification);
CREATE INDEX IF NOT EXISTS idx_commercial_prospect_observations_scan
    ON commercial_prospect_observations(scan_id);
CREATE INDEX IF NOT EXISTS idx_commercial_prospect_observations_lead
    ON commercial_prospect_observations(lead_id, observed_at DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BusinessFinderRateLimitError(Exception):
    """The caller exceeded the configured business-finder request rate."""


class BusinessFinderBudgetLimitError(Exception):
    """The configured daily business-finder query allowance is exhausted."""


class BusinessFinderDuplicateRequestError(Exception):
    """A search request ID has already reserved a provider query."""

    def __init__(self, status: str):
        self.status = status


class BusinessFinderRequestConflictError(Exception):
    """A request ID was reused with different search details."""


class BusinessFinderConfirmationError(Exception):
    """A confirmation token is absent, expired, or already consumed."""


class BusinessFinderConfirmationConflictError(Exception):
    """A confirmation request ID was reused for a different candidate."""


class BusinessFinderDiagnosticConflictError(Exception):
    """A diagnostic request ID conflicts with a prior request."""


def begin_business_finder_search(
    request_id: str,
    query_hash: str,
    provider_id: str,
    subject_hash: str,
    usage_date: str,
    bucket_start: int,
    rate_limit: int,
    daily_limit: int,
    global_subject_hash: str | None = None,
    global_bucket_start: int | None = None,
    global_rate_limit: int | None = None,
) -> None:
    """Reserve limits and a unique request ID before contacting a provider."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "DELETE FROM business_finder_confirmations WHERE expires_at < ?",
            (int(datetime.now(timezone.utc).timestamp()),),
        )
        c.execute(
            "DELETE FROM business_finder_confirmation_requests WHERE expires_at < ?",
            (int(datetime.now(timezone.utc).timestamp()),),
        )
        existing = c.execute(
            "SELECT query_hash, status FROM business_finder_searches WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            if existing["query_hash"] != query_hash:
                raise BusinessFinderRequestConflictError
            raise BusinessFinderDuplicateRequestError(existing["status"])

        daily = c.execute(
            "SELECT query_count FROM business_finder_daily_usage WHERE usage_date = ?",
            (usage_date,),
        ).fetchone()
        if daily_limit <= 0 or (daily and daily["query_count"] >= daily_limit):
            raise BusinessFinderBudgetLimitError

        rate_keys = [(subject_hash, bucket_start, rate_limit)]
        if (
            global_subject_hash is not None
            and global_bucket_start is not None
            and global_rate_limit is not None
        ):
            rate_keys.append(
                (global_subject_hash, global_bucket_start, global_rate_limit)
            )
        for key, start, limit in rate_keys:
            rate = c.execute(
                "SELECT request_count FROM business_finder_rate_buckets "
                "WHERE subject_hash = ? AND bucket_start = ?",
                (key, start),
            ).fetchone()
            if limit <= 0 or (rate and rate["request_count"] >= limit):
                raise BusinessFinderRateLimitError

        created = now()
        c.execute(
            "INSERT INTO business_finder_searches "
            "(request_id, query_hash, provider_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'started', ?, ?)",
            (request_id, query_hash, provider_id, created, created),
        )
        c.execute(
            "INSERT INTO business_finder_daily_usage(usage_date, query_count) VALUES (?, 1) "
            "ON CONFLICT(usage_date) DO UPDATE SET query_count = query_count + 1",
            (usage_date,),
        )
        for key, start, _ in rate_keys:
            c.execute(
                "INSERT INTO business_finder_rate_buckets"
                "(subject_hash, bucket_start, request_count) VALUES (?, ?, 1) "
                "ON CONFLICT(subject_hash, bucket_start) "
                "DO UPDATE SET request_count = request_count + 1",
                (key, start),
            )
        c.execute(
            "DELETE FROM business_finder_rate_buckets WHERE bucket_start < ?",
            (bucket_start - 86400,),
        )


def finish_business_finder_search(
    request_id: str,
    classification: str,
    *,
    uncertain: bool = False,
) -> None:
    with connect() as c:
        c.execute(
            "UPDATE business_finder_searches SET status=?, classification=?, updated_at=? "
            "WHERE request_id=? AND status='started'",
            ("uncertain" if uncertain else "completed", classification, now(), request_id),
        )


def create_business_finder_confirmation(nonce_hash: str, expires_at: int) -> None:
    with connect() as c:
        c.execute(
            "DELETE FROM business_finder_confirmations WHERE expires_at < ?",
            (int(datetime.now(timezone.utc).timestamp()),),
        )
        c.execute(
            "INSERT OR IGNORE INTO business_finder_confirmations"
            "(nonce_hash, expires_at, created_at) VALUES (?, ?, ?)",
            (nonce_hash, expires_at, now()),
        )


def confirm_business_finder_candidate(
    candidate_nonce_hash: str,
    request_id: str,
    input_hash: str,
    confirmation_nonce_hash: str,
    expires_at: int,
    current_time: int,
) -> None:
    """Confirm a candidate once and make the result retryable by request ID."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute(
            "SELECT input_hash FROM business_finder_confirmation_requests "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if previous:
            if previous["input_hash"] != input_hash:
                raise BusinessFinderConfirmationConflictError
            return

        candidate = c.execute(
            "SELECT expires_at, request_id FROM business_finder_confirmations "
            "WHERE nonce_hash = ?",
            (candidate_nonce_hash,),
        ).fetchone()
        if (
            not candidate
            or candidate["expires_at"] < current_time
            or (
                candidate["request_id"] is not None
                and candidate["request_id"] != request_id
            )
        ):
            raise BusinessFinderConfirmationError
        if candidate["request_id"] is None:
            c.execute(
                "UPDATE business_finder_confirmations SET request_id=?, consumed_at=? "
                "WHERE nonce_hash=? AND request_id IS NULL",
                (request_id, now(), candidate_nonce_hash),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise BusinessFinderConfirmationError
        c.execute(
            "INSERT INTO business_finder_confirmation_requests "
            "(request_id, input_hash, confirmation_nonce_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (request_id, input_hash, confirmation_nonce_hash, expires_at, now()),
        )
        c.execute(
            "INSERT OR IGNORE INTO business_finder_confirmations "
            "(nonce_hash, expires_at, created_at) VALUES (?, ?, ?)",
            (confirmation_nonce_hash, expires_at, now()),
        )


def create_business_finder_diagnostic(
    request_id: str,
    request_hash: str,
    confirmation_nonce_hash: str,
    current_time: int,
    business: dict,
    keywords: list[str],
    grid_size: int,
    spacing_km: float,
    provenance: list[dict],
) -> dict:
    """Atomically consume confirmation, persist authorized provenance, and queue scans."""
    from .grid import make_grid, validate_grid

    validate_grid(grid_size, spacing_km)
    if not keywords or len(keywords) > 10:
        raise ValueError("one to ten keywords are required")

    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            "SELECT request_hash, scan_ids FROM business_finder_diagnostics "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise BusinessFinderDiagnosticConflictError
            return {
                "scan_ids": json.loads(prior["scan_ids"]),
                "created_new": False,
            }

        confirmation = c.execute(
            "SELECT expires_at, request_id FROM business_finder_confirmations "
            "WHERE nonce_hash = ?",
            (confirmation_nonce_hash,),
        ).fetchone()
        if (
            not confirmation
            or confirmation["expires_at"] < current_time
            or confirmation["request_id"] is not None
        ):
            raise BusinessFinderConfirmationError

        name = business["name"]
        place_id = business.get("place_id")
        existing = None
        if place_id:
            rows = c.execute(
                "SELECT * FROM businesses WHERE place_id = ?",
                (place_id,),
            ).fetchall()
            matching = [
                row for row in rows
                if row["name"] == name
                and abs(row["lat"] - business["lat"]) <= 0.000001
                and abs(row["lng"] - business["lng"]) <= 0.000001
            ]
            if len(rows) > 1 and len(matching) != 1:
                raise BusinessFinderDiagnosticConflictError
            if rows and not matching:
                raise BusinessFinderDiagnosticConflictError
            existing = matching[0] if matching else None

        if existing:
            business_id = existing["id"]
        else:
            cursor = c.execute(
                "INSERT INTO businesses "
                "(name, cid, place_id, lat, lng, address, category, created) "
                "VALUES (?, NULL, ?, ?, ?, ?, NULL, ?)",
                (
                    name,
                    place_id,
                    business["lat"],
                    business["lng"],
                    business.get("address"),
                    now(),
                ),
            )
            business_id = cursor.lastrowid

        scan_ids: list[int] = []
        grid = make_grid(
            business["lat"],
            business["lng"],
            grid_size,
            spacing_km,
        )
        if any(
            not -90 <= point.lat <= 90 or not -180 <= point.lng <= 180
            for point in grid
        ):
            raise ValueError("business center cannot produce valid grid coordinates")

        created = now()
        for keyword in keywords:
            cursor = c.execute(
                "INSERT INTO scans "
                "(business_id, keyword, grid_size, spacing_km, center_lat, center_lng, "
                "status, progress, total, created, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, 'business_finder')",
                (
                    business_id,
                    keyword,
                    grid_size,
                    spacing_km,
                    business["lat"],
                    business["lng"],
                    grid_size * grid_size,
                    created,
                ),
            )
            scan_id = cursor.lastrowid
            scan_ids.append(scan_id)
            for point in grid:
                c.execute(
                    "INSERT INTO points "
                    "(scan_id, row, col, lat, lng, rank, status, results) "
                    "VALUES (?, ?, ?, ?, ?, NULL, 'pending', '[]')",
                    (scan_id, point.row, point.col, point.lat, point.lng),
                )

        for item in provenance:
            if not item["storage_authorized"]:
                raise ValueError("unauthorized provider data cannot be persisted")
            c.execute(
                "INSERT OR IGNORE INTO business_finder_data_provenance "
                "(business_id, field_name, data_origin, provider_id, license_name, "
                "retrieved_at, valid_until, storage_authorized, attribution) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    business_id,
                    item["field_name"],
                    item["data_origin"],
                    item["provider_id"],
                    item["license_name"],
                    item["retrieved_at"],
                    item.get("valid_until"),
                    item.get("attribution"),
                ),
            )

        provider_reference_id = business.get("provider_reference_id")
        if provider_reference_id:
            if not business.get("provider_reference_storage_authorized"):
                raise ValueError("provider reference is not authorized for storage")
            c.execute(
                "INSERT OR IGNORE INTO business_finder_provider_refs "
                "(business_id, provider_id, provider_reference_id, license_name, "
                "retrieved_at, valid_until, storage_authorized, attribution) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    business_id,
                    business["provider_id"],
                    provider_reference_id,
                    business["license_name"],
                    business["retrieved_at"],
                    business.get("valid_until"),
                    business.get("attribution"),
                ),
            )

        c.execute(
            "UPDATE business_finder_confirmations SET request_id=?, consumed_at=? "
            "WHERE nonce_hash=? AND request_id IS NULL",
            (request_id, now(), confirmation_nonce_hash),
        )
        if c.execute("SELECT changes()").fetchone()[0] != 1:
            raise BusinessFinderConfirmationError
        c.execute(
            "INSERT INTO business_finder_diagnostics "
            "(request_id, request_hash, scan_ids, created_at) VALUES (?, ?, ?, ?)",
            (request_id, request_hash, json.dumps(scan_ids), created),
        )
        return {"scan_ids": scan_ids, "created_new": True}


def get_business_finder_diagnostic(request_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT request_hash, scan_ids FROM business_finder_diagnostics "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return (
            {
                "request_hash": row["request_hash"],
                "scan_ids": json.loads(row["scan_ids"]),
            }
            if row
            else None
        )


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")  # live progress reads don't block per-point writes
        c.executescript(SCHEMA)
        columns = {row["name"] for row in c.execute("PRAGMA table_info(points)")}
        for name, definition in (
            ("error_category", "TEXT"),
            ("api_status_code", "INTEGER"),
            ("api_status_message", "TEXT"),
            ("external_task_id", "TEXT"),
        ):
            if name not in columns:
                c.execute(f"ALTER TABLE points ADD COLUMN {name} {definition}")
        scan_columns = {row["name"] for row in c.execute("PRAGMA table_info(scans)")}
        if "origin" not in scan_columns:
            c.execute("ALTER TABLE scans ADD COLUMN origin TEXT NOT NULL DEFAULT 'web'")
        if "result_provider" not in scan_columns:
            c.execute(
                "ALTER TABLE scans ADD COLUMN result_provider TEXT NOT NULL DEFAULT 'unknown'"
            )
        external_scan_columns = {
            row["name"] for row in c.execute("PRAGMA table_info(external_scans)")
        }
        if "commercial_diagnostic_id" not in external_scan_columns:
            c.execute(
                "ALTER TABLE external_scans ADD COLUMN commercial_diagnostic_id "
                "TEXT REFERENCES commercial_diagnostics(id)"
            )
        diagnostic_columns = {
            row["name"] for row in c.execute("PRAGMA table_info(commercial_diagnostics)")
        }
        for name in ("confirmed_provider_id", "confirmed_reference_id"):
            if name not in diagnostic_columns:
                c.execute(f"ALTER TABLE commercial_diagnostics ADD COLUMN {name} TEXT")
        prospect_observation_columns = {
            row["name"]
            for row in c.execute("PRAGMA table_info(commercial_prospect_observations)")
        }
        prospect_columns = {
            row["name"] for row in c.execute("PRAGMA table_info(commercial_prospects)")
        }
        for name in ("phone", "website"):
            if name not in prospect_columns:
                c.execute(f"ALTER TABLE commercial_prospects ADD COLUMN {name} TEXT")
        if "provider_position" not in prospect_observation_columns:
            c.execute(
                "ALTER TABLE commercial_prospect_observations "
                "ADD COLUMN provider_position INTEGER"
            )
        if "record_status" not in prospect_observation_columns:
            c.execute(
                "ALTER TABLE commercial_prospect_observations ADD COLUMN "
                "record_status TEXT NOT NULL DEFAULT 'previously_known' "
                "CHECK (record_status IN ('new', 'previously_known'))"
            )
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_points_external_task_id "
            "ON points(external_task_id) WHERE external_task_id IS NOT NULL"
        )


def upsert_business(b: dict) -> int:
    with connect() as c:
        if b.get("cid"):
            row = c.execute("SELECT id FROM businesses WHERE cid = ?", (b["cid"],)).fetchone()
            if row:
                return row["id"]
        cur = c.execute(
            "INSERT INTO businesses (name, cid, place_id, lat, lng, address, category, created) VALUES (?,?,?,?,?,?,?,?)",
            (b["name"], b.get("cid"), b.get("place_id"), b["lat"], b["lng"], b.get("address"), b.get("category"), now()))
        return cur.lastrowid


def get_business(business_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
        return dict(row) if row else None


def register_external_business(
    client_id: str,
    external_business_id: str,
    business: dict,
) -> tuple[dict, bool]:
    """Create an external-client mapping without exposing the internal business id."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT b.* FROM external_businesses eb "
            "JOIN businesses b ON b.id = eb.business_id "
            "WHERE eb.client_id = ? AND eb.external_business_id = ?",
            (client_id, external_business_id),
        ).fetchone()
        created = False
        if row:
            saved = dict(row)
            if (
                saved["name"] != business["name"]
                or abs(saved["lat"] - business["lat"]) > 0.000001
                or abs(saved["lng"] - business["lng"]) > 0.000001
                or (
                    business.get("cid") is not None
                    and saved.get("cid") != business["cid"]
                )
                or (
                    business.get("place_id") is not None
                    and saved.get("place_id") != business["place_id"]
                )
            ):
                raise ExternalConflictError(
                    "external_business_id is already registered with different business details"
                )
        else:
            row = None
            if business.get("cid"):
                row = c.execute(
                    "SELECT * FROM businesses WHERE cid = ?",
                    (business["cid"],),
                ).fetchone()
            if row is None and business.get("place_id"):
                row = c.execute(
                    "SELECT * FROM businesses WHERE place_id = ? ORDER BY id LIMIT 1",
                    (business["place_id"],),
                ).fetchone()
            if row:
                saved = dict(row)
                if (
                    saved["name"] != business["name"]
                    or abs(saved["lat"] - business["lat"]) > 0.000001
                    or abs(saved["lng"] - business["lng"]) > 0.000001
                ):
                    raise ExternalConflictError(
                        "the supplied business identifier conflicts with an existing business"
                    )
                business_id = saved["id"]
            else:
                cur = c.execute(
                    "INSERT INTO businesses "
                    "(name, cid, place_id, lat, lng, address, category, created) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        business["name"],
                        business.get("cid"),
                        business.get("place_id"),
                        business["lat"],
                        business["lng"],
                        business.get("address"),
                        business.get("category"),
                        now(),
                    ),
                )
                business_id = cur.lastrowid
                saved = dict(
                    c.execute(
                        "SELECT * FROM businesses WHERE id = ?",
                        (business_id,),
                    ).fetchone()
                )
            c.execute(
                "INSERT INTO external_businesses "
                "(client_id, external_business_id, business_id, created) VALUES (?,?,?,?)",
                (client_id, external_business_id, business_id, now()),
            )
            created = True

        return {
            "business_id": external_business_id,
            "name": saved["name"],
            "cid": saved.get("cid"),
            "place_id": saved.get("place_id"),
            "lat": saved["lat"],
            "lng": saved["lng"],
            "address": saved.get("address"),
            "category": saved.get("category"),
        }, created


def create_external_scan(
    client_id: str,
    external_request_id: str,
    external_business_id: str,
    keyword: str,
    language_code: str,
    *, registered_business_id: int | None = None, grid_size: int = 3, spacing_km: float = 1.0,
) -> tuple[dict, bool]:
    """Create a external scan, or return its idempotent prior record."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT e.external_id, e.external_business_id, e.external_request_id, "
            "s.keyword, s.grid_size, s.spacing_km FROM external_scans e JOIN scans s ON s.id = e.scan_id "
            "WHERE e.client_id = ? AND e.external_request_id = ?",
            (client_id, external_request_id),
        ).fetchone()
        if existing:
            if (
                existing["external_business_id"] != external_business_id
                or existing["keyword"] != keyword
                or existing["grid_size"] != grid_size
                or existing["spacing_km"] != spacing_km
            ):
                raise ExternalConflictError(
                    "external_request_id was already used with different scan details"
                )
            return self_external_scan(c, client_id, existing["external_id"]), False

        if registered_business_id is not None:
            business_row = c.execute(
                "SELECT * FROM businesses WHERE id = ?", (registered_business_id,)
            ).fetchone()
        else:
            business_row = c.execute(
                "SELECT b.* FROM external_businesses eb "
                "JOIN businesses b ON b.id = eb.business_id "
                "WHERE eb.client_id = ? AND eb.external_business_id = ?",
                (client_id, external_business_id),
            ).fetchone()
        if not business_row:
            raise ExternalScanNotFoundError
        business = dict(business_row)
        commercial_diagnostic = c.execute(
            "SELECT d.*, l.business_confirmed, l.business_id AS lead_business_id "
            "FROM commercial_diagnostics d "
            "JOIN commercial_leads l ON l.id = d.lead_id "
            "WHERE d.request_id = ?",
            (external_request_id,),
        ).fetchone()
        if commercial_diagnostic:
            if (
                commercial_diagnostic["product_id"] != "express"
                or commercial_diagnostic["status"] not in {"reserved", "queued"}
                or not commercial_diagnostic["business_confirmed"]
                or registered_business_id is not None
                or commercial_diagnostic["grid_size"] != grid_size
                or commercial_diagnostic["keyword"] != keyword
                or commercial_diagnostic["confirmed_provider_id"] != "google_places"
                or commercial_diagnostic["confirmed_reference_id"] != business.get("place_id")
                or commercial_diagnostic["lead_business_id"] not in (None, business["id"])
                or commercial_diagnostic["business_id"] not in (None, business["id"])
            ):
                raise ExternalConflictError(
                    "external scan does not match the confirmed Express diagnostic"
                )
        created = now()
        cursor = c.execute(
            "INSERT INTO scans "
            "(business_id, keyword, grid_size, spacing_km, center_lat, center_lng, "
            "status, progress, total, created, origin, result_provider) "
            "VALUES (?,?,?,?,?,?,'queued',0,?,?,'external','dataforseo_standard')",
            (
                business["id"],
                keyword,
                grid_size,
                spacing_km,
                business["lat"],
                business["lng"],
                grid_size * grid_size,
                created,
            ),
        )
        scan_id = cursor.lastrowid
        external_id = str(uuid.uuid4())
        c.execute(
            "INSERT INTO external_scans "
            "(external_id, scan_id, client_id, external_request_id, "
            "external_business_id, language_code, status, created, updated, "
            "commercial_diagnostic_id) "
            "VALUES (?,?,?,?,?,?,'queued',?,?,?)",
            (
                external_id,
                scan_id,
                client_id,
                external_request_id,
                external_business_id,
                language_code,
                created,
                created,
                commercial_diagnostic["id"] if commercial_diagnostic else None,
            ),
        )
        if commercial_diagnostic:
            c.execute(
                "UPDATE commercial_diagnostics SET business_id = ?, scan_id = ?, "
                "status = 'queued' WHERE id = ?",
                (business["id"], scan_id, commercial_diagnostic["id"]),
            )
            c.execute(
                "UPDATE commercial_leads SET business_id = ? WHERE id = ?",
                (business["id"], commercial_diagnostic["lead_id"]),
            )
        from .grid import make_grid

        grid = make_grid(
            business["lat"],
            business["lng"],
            grid_size,
            spacing_km,
        )
        if any(
            not -90 <= point.lat <= 90 or not -180 <= point.lng <= 180
            for point in grid
        ):
            raise ExternalInvalidGridError(
                "business center cannot produce a valid grid"
            )
        for point in grid:
            c.execute(
                "INSERT INTO points (scan_id, row, col, lat, lng, rank, status, results) "
                "VALUES (?,?,?,?,?,NULL,'pending','[]')",
                (scan_id, point.row, point.col, point.lat, point.lng),
            )
        return self_external_scan(c, client_id, external_id), True


def self_external_scan(
    c: sqlite3.Connection,
    client_id: str,
    external_id: str,
) -> dict:
    row = c.execute(
        "SELECT e.external_id, e.client_id, e.external_request_id, "
        "e.external_business_id, e.language_code, e.status AS external_status, "
        "e.created AS external_created, e.updated AS external_updated, "
        "e.finished AS external_finished, e.completion_mode, e.completion_reason, "
        "e.commercial_diagnostic_id, "
        "s.*, b.name AS business_name, b.cid AS business_cid, "
        "b.place_id AS business_place_id, b.category AS business_category "
        "FROM external_scans e JOIN scans s ON s.id = e.scan_id "
        "JOIN businesses b ON b.id = s.business_id "
        "WHERE e.client_id = ? AND e.external_id = ?",
        (client_id, external_id),
    ).fetchone()
    if not row:
        raise ExternalScanNotFoundError
    result = _scan_row(row)
    result["points"] = [
        {**dict(point), "results": json.loads(point["results"]) if point["results"] else []}
        for point in c.execute(
            "SELECT * FROM points WHERE scan_id = ? ORDER BY row, col",
            (result["id"],),
        )
    ]
    return result


def get_external_scan(client_id: str, external_id: str) -> dict:
    with connect() as c:
        return self_external_scan(c, client_id, external_id)


def save_external_points(
    client_id: str,
    external_id: str,
    points: list[dict],
) -> dict:
    """Upsert external point outcomes atomically without regressing terminal points."""
    from .matching import find_rank

    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        scan = self_external_scan(c, client_id, external_id)
        if scan["external_finished"]:
            for item in points:
                current = next(
                    (
                        point
                        for point in scan["points"]
                        if point["row"] == item["row"] and point["col"] == item["col"]
                    ),
                    None,
                )
                if not current or not _external_point_matches(current, item):
                    raise ExternalConflictError("scan is finalized; point updates are immutable")
            return {
                "scan": scan,
                "accepted": 0,
                "idempotent": True,
            }

        accepted = 0
        for item in points:
            current = next(
                (
                    point
                    for point in scan["points"]
                    if point["row"] == item["row"] and point["col"] == item["col"]
                ),
                None,
            )
            if not current:
                raise ExternalConflictError("point does not belong to the registered grid")
            if (
                abs(current["lat"] - item["lat"]) > 0.000001
                or abs(current["lng"] - item["lng"]) > 0.000001
            ):
                raise ExternalConflictError("point coordinates do not match the registered grid")
            task_id = item.get("task_id")
            if (
                current.get("external_task_id")
                and task_id
                and current["external_task_id"] != task_id
            ):
                raise ExternalConflictError("a different DataForSEO task is already registered for this point")

            if current["status"] != "pending":
                if _external_point_matches(current, item):
                    continue
                raise ExternalConflictError("a terminal point result cannot be replaced")

            status = item["status"]
            if status == "pending" and _external_point_matches(current, item):
                continue
            results = item.get("results") or []
            rank = find_rank(
                results,
                {
                    "cid": scan.get("business_cid"),
                    "place_id": scan.get("business_place_id"),
                    "name": scan["business_name"],
                },
            ) if status == "ok" else None
            try:
                c.execute(
                    "UPDATE points SET rank=?, status=?, error=?, results=?, error_category=?, "
                    "api_status_code=?, api_status_message=?, external_task_id=? "
                    "WHERE scan_id=? AND row=? AND col=? AND status='pending'",
                    (
                        rank,
                        status,
                        item.get("error"),
                        json.dumps(results, ensure_ascii=False),
                        item.get("error_category"),
                        item.get("api_status_code"),
                        item.get("api_status_message"),
                        task_id or current.get("external_task_id"),
                        scan["id"],
                        item["row"],
                        item["col"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ExternalConflictError(
                    "DataForSEO task id is already assigned to another point"
                ) from error
            accepted += c.execute("SELECT changes()").fetchone()[0]

        point_rows = [
            dict(point)
            for point in c.execute(
                "SELECT * FROM points WHERE scan_id = ? ORDER BY row, col",
                (scan["id"],),
            )
        ]
        progress = sum(point["status"] != "pending" for point in point_rows)
        pending_count = len(point_rows) - progress
        any_task = any(point["external_task_id"] for point in point_rows)
        if not progress and not any_task:
            status = "queued"
        elif pending_count and progress:
            status = "partial"
        else:
            status = "processing"
        updated = now()
        c.execute(
            "UPDATE external_scans SET status=?, updated=? WHERE client_id=? AND external_id=?",
            (status, updated, client_id, external_id),
        )
        c.execute(
            "UPDATE scans SET status=?, progress=? WHERE id=?",
            (status, progress, scan["id"]),
        )
        latest = self_external_scan(c, client_id, external_id)
        return {
            "scan": latest,
            "accepted": accepted,
            "idempotent": accepted == 0,
        }


def _external_point_matches(current: dict, item: dict) -> bool:
    if item.get("task_id") and current.get("external_task_id") != item["task_id"]:
        return False
    if current["status"] != item["status"]:
        return item["status"] == "pending" and current["status"] == "pending"
    if abs(current["lat"] - item["lat"]) > 0.000001:
        return False
    if abs(current["lng"] - item["lng"]) > 0.000001:
        return False
    if current.get("error") != item.get("error"):
        return False
    if current.get("error_category") != item.get("error_category"):
        return False
    if current.get("api_status_code") != item.get("api_status_code"):
        return False
    if current.get("api_status_message") != item.get("api_status_message"):
        return False
    return current.get("results", []) == (item.get("results") or [])


def complete_external_scan(
    client_id: str,
    external_id: str,
    *,
    allow_partial: bool,
    completion_reason: str | None,
    failure_reason: str | None,
) -> dict:
    """Finalize an external scan once; identical finalization requests are idempotent."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        scan = self_external_scan(c, client_id, external_id)
        mode = "failed" if failure_reason else "partial" if allow_partial else "complete"
        reason = failure_reason or completion_reason
        if scan["external_finished"]:
            if (
                scan.get("completion_mode") == mode
                and scan.get("completion_reason") == reason
            ):
                return {"scan": scan, "idempotent": True}
            raise ExternalConflictError("scan was already finalized with different completion details")

        points = scan["points"]
        pending = sum(point["status"] == "pending" for point in points)
        if pending and not allow_partial and not failure_reason:
            raise ExternalPendingPointsError(pending)
        if pending and not reason:
            raise ExternalConflictError("a reason is required to close a scan with pending points")

        valid = [point for point in points if point["status"] == "ok"]
        no_results = sum(point["status"] == "no_results" for point in points)
        failed = sum(point["status"] == "error" for point in points)
        if failure_reason or (failed and not valid and not no_results):
            status = "failed"
        elif pending or failed:
            status = "partial"
        else:
            status = "done"

        from .metrics import competitor_leaderboard, scan_coverage, scan_metrics

        metrics = scan_metrics([point["rank"] for point in valid])
        metrics.update(scan_coverage(points, scan["total"]))
        metrics["errors"] = metrics["failed_points"]
        competitors = competitor_leaderboard(
            [point["results"] for point in valid]
        )
        finished = now()
        c.execute(
            "UPDATE scans SET status=?, progress=?, metrics=?, competitors=?, error=?, finished=? "
            "WHERE id=?",
            (
                status,
                len(points) - pending,
                json.dumps(metrics),
                json.dumps(competitors),
                reason if status in {"partial", "failed"} else None,
                finished,
                scan["id"],
            ),
        )
        c.execute(
            "UPDATE external_scans SET status=?, updated=?, finished=?, "
            "completion_mode=?, completion_reason=? WHERE client_id=? AND external_id=?",
            (
                status,
                finished,
                finished,
                mode,
                reason,
                client_id,
                external_id,
            ),
        )
        diagnostic_id = scan.get("commercial_diagnostic_id")
        if diagnostic_id:
            c.execute(
                "UPDATE commercial_diagnostics SET status = ? WHERE id = ? "
                "AND status IN ('reserved', 'queued', 'running')",
                (
                    "done" if status == "done" else "failed",
                    diagnostic_id,
                ),
            )
        return {
            "scan": self_external_scan(c, client_id, external_id),
            "idempotent": False,
        }


def external_database_health() -> bool:
    with connect() as c:
        return c.execute("SELECT 1").fetchone()[0] == 1


def list_businesses() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM businesses ORDER BY name")]


def create_scan(business_id: int, keyword: str, grid_size: int, spacing_km: float,
                center_lat: float, center_lng: float) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO scans (business_id, keyword, grid_size, spacing_km, center_lat, center_lng, total, created)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (business_id, keyword, grid_size, spacing_km, center_lat, center_lng, grid_size * grid_size, now()))
        return cur.lastrowid


def set_scan_result_provider(scan_id: int, provider_id: str) -> None:
    provider_id = provider_id.strip().lower()
    if not provider_id or len(provider_id) > 100:
        raise ValueError("provider_id must contain between 1 and 100 characters")
    with connect() as c:
        c.execute(
            "UPDATE scans SET result_provider = ? WHERE id = ?",
            (provider_id, scan_id),
        )


def save_point(
    scan_id: int,
    p,
    rank: int | None,
    status: str,
    results: list[dict] | None,
    error: str | None,
    *,
    error_category: str | None = None,
    api_status_code: int | None = None,
    api_status_message: str | None = None,
) -> None:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT id, status FROM points WHERE scan_id = ? AND row = ? AND col = ? "
            "ORDER BY id LIMIT 1",
            (scan_id, p.row, p.col),
        ).fetchone()
        if existing and existing["status"] != "pending":
            return
        values = (
            p.lat,
            p.lng,
            rank,
            status,
            error,
            json.dumps(results) if results is not None else None,
            error_category,
            api_status_code,
            api_status_message,
        )
        if existing:
            c.execute(
                "UPDATE points SET lat = ?, lng = ?, rank = ?, status = ?, error = ?, "
                "results = ?, error_category = ?, api_status_code = ?, "
                "api_status_message = ? WHERE id = ?",
                (*values, existing["id"]),
            )
        else:
            c.execute(
                "INSERT INTO points (scan_id, row, col, lat, lng, rank, status, error, results,"
                " error_category, api_status_code, api_status_message) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scan_id,
                    p.row,
                    p.col,
                    *values,
                ),
            )
        c.execute("UPDATE scans SET progress = progress + 1 WHERE id = ?", (scan_id,))


def finish_scan(scan_id: int, status: str, metrics: dict | None, competitors: list | None,
                error: str | None = None) -> None:
    with connect() as c:
        c.execute("UPDATE scans SET status=?, metrics=?, competitors=?, error=?, finished=? WHERE id=?",
                  (status, json.dumps(metrics) if metrics else None,
                   json.dumps(competitors) if competitors else None, error, now(), scan_id))


def mark_interrupted_scans() -> None:
    """Scans left 'running' by a previous process will never finish."""
    with connect() as c:
        c.execute(
            "UPDATE scans SET status='failed', error='interrupted', finished=? "
            "WHERE status='running' AND origin != 'external'",
            (now(),),
        )


def _scan_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("metrics", "competitors"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def list_scans(business_id: int | None = None, limit: int = 100) -> list[dict]:
    # Leaves out the (large) competitors column: lists only need metrics.
    sql = ("SELECT s.id, s.business_id, s.keyword, s.grid_size, s.spacing_km, s.center_lat, s.center_lng, s.status,"
           " s.progress, s.total, s.metrics, s.error, s.created, s.finished, b.name AS business_name"
           " FROM scans s JOIN businesses b ON b.id = s.business_id"
           + (" WHERE s.business_id = ?" if business_id else "") + " ORDER BY s.id DESC LIMIT ?")
    args = (business_id, limit) if business_id else (limit,)
    with connect() as c:
        return [_scan_row(r) for r in c.execute(sql, args)]


def get_scan(scan_id: int, with_points: bool = True) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT s.*, b.name AS business_name, b.cid AS business_cid, b.lat AS business_lat,"
            " b.lng AS business_lng, b.place_id AS business_place_id, b.category AS business_category FROM scans s JOIN businesses b ON b.id = s.business_id WHERE s.id = ?",
            (scan_id,)).fetchone()
        if not row:
            return None
        scan = _scan_row(row)
        if with_points:
            scan["points"] = [
                {**dict(p), "results": json.loads(p["results"]) if p["results"] else []}
                for p in c.execute("SELECT * FROM points WHERE scan_id = ? ORDER BY row, col", (scan_id,))]
        return scan


def previous_scan_id(scan: dict) -> int | None:
    """Most recent finished scan of the same business + keyword + grid before this one."""
    with connect() as c:
        row = c.execute(
            "SELECT id FROM scans WHERE business_id=? AND keyword=? AND grid_size=? AND spacing_km=?"
            " AND status='done' AND id < ? ORDER BY id DESC LIMIT 1",
            (scan["business_id"], scan["keyword"], scan["grid_size"], scan["spacing_km"], scan["id"])).fetchone()
        return row["id"] if row else None


def delete_scan(scan_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
