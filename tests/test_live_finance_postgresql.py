"""Integration tests against the migrated, disposable P5.10 PostgreSQL database."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db, live_finance as finance, main
from app.fetchers.base import FetchStatusError
from app.fetchers.dataforseo import DataForSEOFetcher


@pytest.fixture
def ledger(monkeypatch):
    dsn = os.environ.get("GEOGRID_TEST_LIVE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires a disposable migrated PostgreSQL database")
    monkeypatch.setenv("GEOGRID_FINANCE_DATABASE_URL", dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE geogrid_live_scan_queries, geogrid_live_scans")
        conn.execute("DELETE FROM geogrid_budget WHERE scope_type != 'system'")
        conn.execute("""
            UPDATE geogrid_budget
            SET enabled = true, daily_cap_usd = 1, default_event_cap_usd = 1,
                estimated_live_task_cost_usd = .01
            WHERE scope_type = 'system' AND scope_key = 'default'
        """)
    return dsn


def reserve(key, fingerprint="body", keywords=2):
    return finance.reserve_live_scan(
        idempotency_key=key, request_fingerprint=fingerprint, operator_id="operator.test",
        business_id=1, grid_size=3, keyword_count=keywords,
    )


def balances(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute("""
            SELECT reserved_amount_usd, spent_amount_usd FROM geogrid_budget
            WHERE scope_type IN ('daily', 'event') ORDER BY scope_type
        """).fetchall()


def race(*calls):
    ready = Barrier(len(calls))

    def run(call):
        ready.wait(timeout=5)
        try:
            return call()
        except finance.LiveBudgetDenied as error:
            return error

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def authorize(reservation, point, *, operator="operator.test"):
    return finance.authorize_live_query(
        idempotency_key=reservation.idempotency_key, claim_token=reservation.claim_token,
        operator_id=operator, scan_index=0, scan_id=101, point_index=point,
    )


def test_concurrent_reservation_idempotency_and_limits(ledger):
    key = uuid4()
    first, second = race(lambda: reserve(key), lambda: reserve(key))
    assert {first.created, second.created} == {True, False}
    assert first.reserved_usd == second.reserved_usd == Decimal(".18")
    assert balances(ledger) == [(Decimal(".18"), 0)] * 2
    with pytest.raises(finance.LiveIdempotencyConflict):
        reserve(key, fingerprint="different")
    with pytest.raises(finance.LiveBudgetDenied):
        reserve(uuid4())
    assert finance.finish_live_scan(key) == "failed"
    with psycopg.connect(ledger) as conn:
        conn.execute("UPDATE geogrid_budget SET daily_cap_usd = .17 WHERE scope_type = 'system'")
    with pytest.raises(finance.LiveBudgetDenied):
        reserve(uuid4())
    assert balances(ledger) == [(0, 0)] * 2
    with psycopg.connect(ledger) as conn:
        conn.execute("UPDATE geogrid_budget SET daily_cap_usd = 1 WHERE scope_type = 'system'")
    outcomes = race(lambda: reserve(uuid4()), lambda: reserve(uuid4()))
    assert sum(isinstance(x, finance.LiveReservation) for x in outcomes) == 1
    assert balances(ledger) == [(Decimal(".18"), 0)] * 2


def test_concurrent_query_uniqueness_and_uncertain_reconciliation(ledger):
    reservation = reserve(uuid4(), keywords=1)
    key = reservation.idempotency_key
    assert not authorize(reservation, 0)
    assert finance.attach_live_scan_ids(key, reservation.claim_token, [101])
    assert not authorize(reservation, 0, operator="another")
    assert sorted(race(lambda: authorize(reservation, 0), lambda: authorize(reservation, 0))) == [False, True]
    assert authorize(reservation, 1)
    assert not authorize(reservation, 2)
    params = dict(idempotency_key=key, scan_index=0, point_index=0, actual_cost_usd=Decimal(".007"))
    assert race(lambda: finance.record_live_query_cost(**params), lambda: finance.record_live_query_cost(**params)) == [True, True]
    assert balances(ledger) == [(Decimal(".08"), Decimal(".007"))] * 2
    assert finance.mark_live_query_uncertain(key, 0, 1)
    assert not authorize(reservation, 2)
    assert finance.finish_live_scan(key) == "uncertain"
    with pytest.raises(finance.LiveBudgetDenied):
        reserve(uuid4())
    with pytest.raises(finance.LiveBudgetDenied):
        finance.reconcile_live_scan(key, total_cost_usd=Decimal(".006"), evidence_reference="fixture")
    finance.reconcile_live_scan(key, total_cost_usd=Decimal(".015"), evidence_reference="fixture")
    assert balances(ledger) == [(0, Decimal(".015"))] * 2


def test_overrun_and_system_stop_block_further_queries(ledger):
    reservation = reserve(uuid4(), keywords=1)
    key = reservation.idempotency_key
    assert finance.attach_live_scan_ids(key, reservation.claim_token, [101])
    assert authorize(reservation, 0)
    assert authorize(reservation, 1)
    with psycopg.connect(ledger) as conn:
        conn.execute("UPDATE geogrid_budget SET enabled = false WHERE scope_type = 'system'")
    finance.record_live_query_cost(
        idempotency_key=key, scan_index=0, point_index=0, actual_cost_usd=Decimal(".02"),
    )
    with pytest.raises(finance.LiveBudgetDenied):
        finance.reconcile_live_scan(key, total_cost_usd=Decimal(".02"), evidence_reference="fixture")
    assert finance.mark_live_query_uncertain(key, 0, 1)
    assert finance.finish_live_scan(key) == "cost_overrun"
    with psycopg.connect(ledger) as conn:
        conn.execute("UPDATE geogrid_budget SET enabled = true WHERE scope_type = 'system'")
    assert not authorize(reservation, 2)
    with pytest.raises(finance.LiveBudgetDenied):
        reserve(uuid4())
    finance.reconcile_live_scan(key, total_cost_usd=Decimal(".03"), evidence_reference="fixture")
    assert balances(ledger) == [(0, Decimal(".03"))] * 2


def test_operator_route_reserves_before_provider_and_replays(ledger, tmp_path, monkeypatch):
    for setting in (
        "GEOGRID_MANUAL_LIVE_ENABLED", "ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED",
        "ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED", "ZURI_GOOGLE_UPSTREAM_USE_APPROVED",
        "GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED",
    ):
        monkeypatch.setenv(setting, "true")
    monkeypatch.setenv("GEOGRID_FETCHER", " DataForSEO ")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "simulated")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "simulated")
    monkeypatch.setenv("GEOGRID_COMMERCE_OPERATOR_TOKEN", "a" * 40)
    monkeypatch.setenv("GEOGRID_COMMERCE_OPERATOR_IDS", "operator.test")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live.db")

    calls = []
    active = peak = 0

    class Provider:
        async def _fetch_with_cost(self, keyword, lat, lng):
            nonlocal active, peak
            with psycopg.connect(ledger) as conn:
                state = conn.execute("""
                    SELECT count(*) FROM geogrid_live_scan_queries WHERE state = 'authorized'
                """).fetchone()[0]
            assert state >= 1
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.015)
                calls.append((keyword, lat, lng))
                return [{"name": "Fixture", "cid": "test"}], Decimal(".008")
            finally:
                active -= 1

    monkeypatch.setattr(main, "fetcher", lambda: Provider())
    path = "/api/commercial/admin/live-scans"
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(main.app, base_url="https://testserver") as api:
        business_id = db.upsert_business({"name": "Fixture", "cid": "test", "lat": -23.5, "lng": -46.6})
        payload = {"business_id": business_id, "keywords": ["first", "second"], "grid_size": 3}
        assert api.post(path, headers=headers, json=payload).status_code == 401
        assert api.post("/api/scans", json=payload).status_code == 403
        assert api.post("/api/commercial/admin/login", json={
            "operator_id": "operator.test", "operator_token": "a" * 40,
        }).status_code == 200
        assert api.post(path, headers={**headers, "Origin": "https://invalid.test"}, json=payload).status_code == 403
        with monkeypatch.context() as temporary:
            temporary.setenv("GEOGRID_COMMERCE_OPERATOR_IDS", "another")
            assert api.post(path, headers=headers, json=payload).status_code == 403
        with monkeypatch.context() as temporary:
            temporary.delenv("GEOGRID_FINANCE_DATABASE_URL")
            assert api.post(path, headers=headers, json=payload).status_code == 503
        assert not calls and db.list_scans(business_id) == []
        result = api.post(path, headers=headers, json=payload)
        assert result.status_code == 202
        scan_ids = result.json()["scan_ids"]
        assert len(scan_ids) == 2
        assert all(db.get_scan(scan_id)["result_provider"] == "dataforseo_live" for scan_id in scan_ids)
        assert api.post(path, headers=headers, json=payload).json() == {
            "scan_ids": scan_ids, "status": "completed",
        }
        assert api.post(path, headers=headers, json={**payload, "keywords": ["changed"]}).status_code == 409
    assert len(calls) == len(set(calls)) == 18
    assert peak == 2
    assert balances(ledger) == [(0, Decimal(".144"))] * 2


@pytest.mark.asyncio
async def test_unreserved_fetch_is_blocked(monkeypatch):
    fetcher = DataForSEOFetcher(resolver=None, login="fixture", password="fixture")

    async def forbidden(*args):
        pytest.fail("unreserved fetch must not call the provider")

    monkeypatch.setattr(fetcher, "_fetch_with_cost", forbidden)
    try:
        with pytest.raises(FetchStatusError, match="financial reservation"):
            await fetcher.fetch("fixture", 0, 0)
    finally:
        await fetcher._client.aclose()
