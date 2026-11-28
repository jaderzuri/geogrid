import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_historical_sqlite.py"


def test_historical_validator_preserves_source_and_reports_contacts(tmp_path):
    source = tmp_path / "synthetic-legacy.sqlite3"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, cid TEXT, place_id TEXT,
            lat REAL NOT NULL, lng REAL NOT NULL, address TEXT, category TEXT,
            created TEXT NOT NULL
        );
        CREATE TABLE scans (
            id INTEGER PRIMARY KEY, business_id INTEGER NOT NULL REFERENCES businesses(id),
            keyword TEXT NOT NULL, grid_size INTEGER NOT NULL, spacing_km REAL NOT NULL,
            center_lat REAL NOT NULL, center_lng REAL NOT NULL, status TEXT NOT NULL,
            progress INTEGER NOT NULL, total INTEGER NOT NULL, metrics TEXT,
            competitors TEXT, error TEXT, created TEXT NOT NULL, finished TEXT
        );
        CREATE TABLE points (
            id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scans(id),
            row INTEGER NOT NULL, col INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
            rank INTEGER, status TEXT NOT NULL, error TEXT, results TEXT
        );
        INSERT INTO businesses VALUES (
            1, 'Synthetic Co', 'synthetic-cid', 'synthetic-place',
            -23.5, -46.6, 'synthetic address', 'synthetic category', '2026-01-01'
        );
        INSERT INTO scans VALUES (
            1, 1, 'synthetic query', 3, 1, -23.5, -46.6, 'done', 1, 1,
            '{"visibility": 100}', '[]', NULL, '2026-01-01', '2026-01-01'
        );
        """
    )
    connection.execute(
        "INSERT INTO points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            1,
            0,
            0,
            -23.5,
            -46.6,
            1,
            "ok",
            None,
            json.dumps(
                [
                    {
                        "name": "Synthetic Co",
                        "phone": "+5511000000000",
                        "website": "https://example.invalid",
                        "position": 7,
                    }
                ]
            ),
        ),
    )
    connection.commit()
    connection.close()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), str(source)],
        check=True,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["source_open_mode"] == "read-only"
    assert report["preservation_errors"] == []
    assert report["second_initialization_idempotent"] is True
    assert report["before"]["tables"]["businesses"] == 1
    assert report["before"]["tables"]["scans"] == 1
    assert report["before"]["tables"]["points"] == 1
    assert report["before"]["contact_field_observations"]["points.results"] == {
        "candidate_records": 1,
        "candidates_with_phone": 1,
        "candidates_with_website": 1,
        "candidates_with_provider_position": 1,
    }
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_historical_validator_rejects_empty_database(tmp_path):
    source = tmp_path / "empty.sqlite3"
    sqlite3.connect(source).close()

    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), str(source)],
        capture_output=True,
        cwd=ROOT,
        text=True,
    )

    assert completed.returncode != 0
    assert "populated businesses, scans, and points tables" in completed.stderr
