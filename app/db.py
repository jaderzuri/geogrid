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
    origin TEXT NOT NULL DEFAULT 'web'
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
    UNIQUE (client_id, external_request_id)
);
CREATE INDEX IF NOT EXISTS idx_points_scan ON points(scan_id);
CREATE INDEX IF NOT EXISTS idx_scans_business ON scans(business_id, keyword);
CREATE INDEX IF NOT EXISTS idx_external_business_client ON external_businesses(client_id, external_business_id);
CREATE INDEX IF NOT EXISTS idx_external_scan_client ON external_scans(client_id, external_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
) -> tuple[dict, bool]:
    """Create a fixed 3x3 external scan, or return its idempotent prior record."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT e.external_id, e.external_business_id, e.external_request_id, "
            "s.keyword FROM external_scans e JOIN scans s ON s.id = e.scan_id "
            "WHERE e.client_id = ? AND e.external_request_id = ?",
            (client_id, external_request_id),
        ).fetchone()
        if existing:
            if (
                existing["external_business_id"] != external_business_id
                or existing["keyword"] != keyword
            ):
                raise ExternalConflictError(
                    "external_request_id was already used with different scan details"
                )
            return self_external_scan(c, client_id, existing["external_id"]), False

        business_row = c.execute(
            "SELECT b.* FROM external_businesses eb "
            "JOIN businesses b ON b.id = eb.business_id "
            "WHERE eb.client_id = ? AND eb.external_business_id = ?",
            (client_id, external_business_id),
        ).fetchone()
        if not business_row:
            raise ExternalScanNotFoundError
        business = dict(business_row)
        grid_size, spacing_km = 3, 1.0
        created = now()
        cursor = c.execute(
            "INSERT INTO scans "
            "(business_id, keyword, grid_size, spacing_km, center_lat, center_lng, "
            "status, progress, total, created, origin) "
            "VALUES (?,?,?,?,?,?,'queued',0,9,?,'external')",
            (
                business["id"],
                keyword,
                grid_size,
                spacing_km,
                business["lat"],
                business["lng"],
                created,
            ),
        )
        scan_id = cursor.lastrowid
        external_id = str(uuid.uuid4())
        c.execute(
            "INSERT INTO external_scans "
            "(external_id, scan_id, client_id, external_request_id, "
            "external_business_id, language_code, status, created, updated) "
            "VALUES (?,?,?,?,?,?,'queued',?,?)",
            (
                external_id,
                scan_id,
                client_id,
                external_request_id,
                external_business_id,
                language_code,
                created,
                created,
            ),
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
                "business center cannot produce a valid 3x3 grid"
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
        c.execute(
            "INSERT INTO points (scan_id, row, col, lat, lng, rank, status, error, results,"
            " error_category, api_status_code, api_status_message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan_id, p.row, p.col, p.lat, p.lng, rank, status, error,
             json.dumps(results) if results is not None else None,
             error_category, api_status_code, api_status_message))
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
