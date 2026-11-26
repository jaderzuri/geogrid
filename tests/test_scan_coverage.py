import asyncio
import sqlite3

import pytest

from app import db
from app.fetchers.base import FetchStatusError
from app.metrics import scan_coverage
from app.scan import run_scan


@pytest.fixture
def temporary_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "geogrid-test.db")
    db.init_db()
    business_id = db.upsert_business({
        "name": "Target business",
        "cid": "target-cid",
        "lat": -23.5,
        "lng": -46.6,
    })
    scan_id = db.create_scan(business_id, "serviço local", 3, 1, -23.5, -46.6)
    return scan_id


class ScriptedFetcher:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    async def fetch(self, keyword, lat, lng):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_scan_keeps_valid_absence_separate_from_no_results_errors_and_pending(temporary_db):
    outcomes = [
        [{"name": "Target business", "cid": "target-cid"}],
        [{"name": "Another business", "cid": "other-cid"}],
        FetchStatusError(
            "DataForSEO 40102: No Search Results",
            outcome="no_results",
            category="no_results",
            api_status_code=40102,
            api_status_message="No Search Results",
        ),
        FetchStatusError(
            "DataForSEO 40200: Payment required",
            category="insufficient_balance",
            api_status_code=40200,
            api_status_message="Payment required",
        ),
        *([[{"name": "Competitor", "cid": f"competitor-{index}"}]] for index in range(5)),
    ]

    asyncio.run(run_scan(temporary_db, ScriptedFetcher(outcomes), concurrency=1))
    scan = db.get_scan(temporary_db)
    points = {(point["row"], point["col"]): point for point in scan["points"]}

    assert scan["status"] == "done"
    assert scan["metrics"]["requested_points"] == 9
    assert scan["metrics"]["valid_points"] == 2
    assert scan["metrics"]["no_results_points"] == 1
    assert scan["metrics"]["failed_points"] == 1
    assert scan["metrics"]["pending_points"] == 5
    assert scan["metrics"]["coverage_pct"] == pytest.approx(22.2)
    assert scan["metrics"]["coverage_level"] == "insufficient"
    assert points[(0, 0)]["rank"] == 1
    assert points[(0, 1)]["rank"] is None
    assert points[(0, 1)]["status"] == "ok"
    assert points[(0, 2)]["status"] == "no_results"
    assert points[(0, 2)]["api_status_code"] == 40102
    assert points[(0, 2)]["api_status_message"] == "No Search Results"
    assert points[(1, 0)]["status"] == "error"
    assert points[(1, 0)]["error_category"] == "insufficient_balance"
    assert points[(1, 0)]["api_status_code"] == 40200
    assert scan["metrics"]["found_pct"] == pytest.approx(50)
    assert scan["metrics"]["atrp"] == pytest.approx(11)


def test_init_db_migrates_point_metadata_additively(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, cid TEXT UNIQUE,
            place_id TEXT, lat REAL NOT NULL, lng REAL NOT NULL,
            address TEXT, category TEXT, created TEXT NOT NULL
        );
        CREATE TABLE scans (
            id INTEGER PRIMARY KEY, business_id INTEGER NOT NULL,
            keyword TEXT NOT NULL, grid_size INTEGER NOT NULL,
            spacing_km REAL NOT NULL, center_lat REAL NOT NULL,
            center_lng REAL NOT NULL, status TEXT NOT NULL DEFAULT 'running',
            progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
            metrics TEXT, competitors TEXT, error TEXT, created TEXT NOT NULL,
            finished TEXT
        );
        CREATE TABLE points (
            id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL, row INTEGER NOT NULL,
            col INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
            rank INTEGER, status TEXT NOT NULL, error TEXT, results TEXT
        );
        INSERT INTO businesses (id, name, lat, lng, created)
        VALUES (1, 'Historical business', 1, 2, '2025-01-01T00:00:00+00:00');
        INSERT INTO scans (id, business_id, keyword, grid_size, spacing_km,
                           center_lat, center_lng, total, created)
        VALUES (1, 1, 'keyword', 3, 1, 1, 2, 9, '2025-01-01T00:00:00+00:00');
        INSERT INTO points (id, scan_id, row, col, lat, lng, rank, status)
        VALUES (1, 1, 0, 0, 1, 2, 3, 'ok');
        """
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(db, "DB_PATH", database_path)

    db.init_db()
    scan = db.get_scan(1)

    assert len(scan["points"]) == 1
    assert scan["points"][0]["rank"] == 3
    assert scan["points"][0]["api_status_code"] is None
    assert scan["points"][0]["api_status_message"] is None


def test_all_no_results_finish_without_fabricating_rank_21(temporary_db):
    outcomes = [
        FetchStatusError(
            "DataForSEO 40102: No Search Results",
            outcome="no_results",
            category="no_results",
            api_status_code=40102,
            api_status_message="No Search Results",
        )
        for _ in range(9)
    ]

    asyncio.run(run_scan(temporary_db, ScriptedFetcher(outcomes), concurrency=1))
    scan = db.get_scan(temporary_db)

    assert scan["status"] == "done"
    assert scan["metrics"]["coverage_pct"] == 0
    assert scan["metrics"]["coverage_level"] == "no_valid_measurement"
    assert scan["metrics"]["no_results_points"] == 9
    assert scan["metrics"]["atrp"] is None
    assert all(point["status"] == "no_results" for point in scan["points"])


@pytest.mark.parametrize(
    ("valid", "total", "expected"),
    [
        (10, 10, "complete"),
        (8, 10, "high"),
        (5, 10, "moderate"),
        (4, 10, "insufficient"),
        (0, 10, "no_valid_measurement"),
    ],
)
def test_scan_coverage_operational_bands(valid, total, expected):
    points = [{"status": "ok"} for _ in range(valid)]
    points.extend({"status": "error"} for _ in range(total - valid))

    result = scan_coverage(points, total)

    assert result["coverage_level"] == expected
