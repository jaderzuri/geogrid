from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest


def test_expired_and_failed_crm_claim_blocks_other_workers():
    dsn = os.environ.get("GEOGRID_TEST_LIVE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires a disposable PostgreSQL database migrated through 010")

    request = uuid4()
    with psycopg.connect(dsn) as connection:
        connection.execute("""
            INSERT INTO geogrid_requests (
                request_id, idempotency_key, payload_fingerprint, campaign_id,
                contact_name, company_name, email, whatsapp, city, state,
                google_maps_url, keyword, consent_at
            ) VALUES (%s, %s, 'f', 'fixture', 'C', 'Co', 'test@example.invalid',
                      '000', 'City', 'ST', 'https://example.invalid', 'test', now())
        """, (request, str(request)))
        for suffix in ("first", "second"):
            connection.execute("""
                INSERT INTO geogrid_crm_outbox (request_id, event_key, event_type, payload)
                VALUES (%s, %s, 'lead_received', '{}')
                ON CONFLICT (event_key) DO NOTHING
            """, (request, f"{request}-{suffix}"))

    ready = Barrier(2)

    def claim(worker):
        with psycopg.connect(dsn) as conn:
            ready.wait(timeout=5)
            return conn.execute("""
                SELECT event_id, claim_token FROM geogrid_claim_crm_outbox(%s, 25)
            """, (worker,)).fetchall()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(claim, ("one", "two"))
        assert sorted((len(first), len(second))) == [0, 1]
        event, token = (first or second)[0]
        with psycopg.connect(dsn) as conn:
            conn.execute("""
                UPDATE geogrid_crm_outbox SET claim_expires_at = now() - interval '1 day'
                WHERE event_id = %s
            """, (event,))
            assert conn.execute(
                "SELECT geogrid_mark_crm_outbox_failure(%s, %s, 'transport', 'fixture', TRUE)",
                (event, token),
            ).fetchone() == ("claimed",)
            assert conn.execute(
                "SELECT event_id FROM geogrid_claim_crm_outbox('another', 25)"
            ).fetchall() == []
            assert conn.execute(
                "SELECT geogrid_mark_crm_outbox_delivered(%s, %s, 'verified-fixture')",
                (event, token),
            ).fetchone() == (True,)
            assert len(conn.execute(
                "SELECT event_id FROM geogrid_claim_crm_outbox('another', 25)"
            ).fetchall()) == 1
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM geogrid_crm_outbox WHERE request_id = %s", (request,))
            conn.execute("DELETE FROM geogrid_requests WHERE request_id = %s", (request,))


def test_finance_panel_requires_session_and_reads_ledger(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import db, main

    dsn = os.environ.get("GEOGRID_TEST_LIVE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires a disposable PostgreSQL database migrated through 010")
    monkeypatch.setenv("GEOGRID_FINANCE_DATABASE_URL", dsn)
    monkeypatch.setenv("GEOGRID_COMMERCE_OPERATOR_TOKEN", "b" * 40)
    monkeypatch.setenv("GEOGRID_COMMERCE_OPERATOR_IDS", "operator.finance")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance-panel.db")
    with TestClient(main.app, base_url="https://testserver") as client:
        path = "/api/commercial/admin/finance"
        assert client.get(path).status_code == 401
        assert client.post("/api/commercial/admin/login", json={
            "operator_id": "operator.finance", "operator_token": "b" * 40,
        }).status_code == 200
        response = client.get(path)
        assert response.status_code == 200
        assert set(response.json()["balances"]) == {"daily", "manual_live"}
        assert response.headers["cache-control"] == "no-store"
        assert "postgresql://" not in response.text
