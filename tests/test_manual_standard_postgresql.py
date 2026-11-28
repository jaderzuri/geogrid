import os
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import db, main, server_reports


def test_manual_standard_batch_1470_persistent_tasks(monkeypatch, tmp_path):
    dsn = os.environ.get("GEOGRID_TEST_LIVE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires disposable PostgreSQL migrated through 011")
    with psycopg.connect(dsn) as connection:
        installed = connection.execute("SELECT to_regclass('geogrid_manual_standard_batches')").fetchone()[0]
        if not installed:
            pytest.skip("requires migration 011")
        connection.execute("""
            UPDATE geogrid_budget SET enabled = true, manual_standard_enabled = true,
                estimated_task_cost_usd = .001, estimated_priority_task_cost_usd = .002,
                daily_cap_usd = 2, default_event_cap_usd = 2, max_pending_tasks = 49,
                new_diagnostics_per_minute = 100
            WHERE scope_type = 'system'
        """)
        connection.execute("DELETE FROM geogrid_budget WHERE scope_type = 'daily'")
        connection.execute("""
            INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
            VALUES ('event','manual-standard',2)
            ON CONFLICT (scope_type,scope_key) DO UPDATE SET cap_amount_usd = 2,
                spent_amount_usd = 0, reserved_amount_usd = 0
        """)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "manual.db")
    for key, value in {
        "GEOGRID_FINANCE_DATABASE_URL": dsn,
        "GEOGRID_MANUAL_STANDARD_ENABLED": "true",
        "ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED": "true",
        "ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED": "true",
        "ZURI_GOOGLE_UPSTREAM_USE_APPROVED": "true",
        "GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED": "true",
        "GEOGRID_COMMERCE_OPERATOR_TOKEN": "t" * 40,
        "GEOGRID_COMMERCE_OPERATOR_IDS": "operator.test",
        "GEOGRID_EXTERNAL_API_TOKEN": "isolated-standard-test",
    }.items():
        monkeypatch.setenv(key, value)
    path = "/api/commercial/admin/standard-scans"
    worker = {
        "Authorization": "Bearer isolated-standard-test",
        "X-GeoGrid-Client-ID": "t2s-landing",
    }
    batch = uuid4()
    with TestClient(main.app, base_url="https://testserver") as client:
        csv_rows = ["business_id,keywords,grid_size,spacing_km"]
        for i in range(10):
            business = db.upsert_business({"name": f"Empresa {i}", "lat": -23.5 + i / 100, "lng": -46.6})
            csv_rows.append(f"{business},padaria|pão integral|café,7,1")
        payload = {"csv_text": "\n".join(csv_rows)}
        assert client.post(path + "/quote", json=payload).status_code == 401
        assert client.post("/api/commercial/admin/login", json={
            "operator_id": "operator.test", "operator_token": "t" * 40,
        }).status_code == 200
        quote = client.post(path + "/quote", json=payload)
        assert quote.status_code == 200, quote.text
        assert quote.json()["queries"] == 1470
        assert Decimal(quote.json()["total_usd"]) == Decimal("1.47")
        priority = client.post(path + "/quote", json={**payload, "modality": "priority"})
        assert Decimal(priority.json()["total_usd"]) == Decimal("2.94")
        headers = {"Idempotency-Key": str(batch)}
        for _ in range(2):
            resp = client.post(path, json={**payload, "approved_total_usd": "1.47"}, headers=headers)
            assert resp.status_code == 202, resp.text

        for index in range(30):
            with psycopg.connect(dsn, row_factory=dict_row) as connection:
                task = connection.execute("SELECT * FROM geogrid_claim_processor_request(%s)", (f"claim-{index}",)).fetchone()
                assert task and task["manual_batch_id"] == batch
                assert task["budget_reserved_usd"] == Decimal(".049")
                assert connection.execute(
                    "SELECT request_id FROM geogrid_claim_processor_request('capacity-check')"
                ).fetchone() is None

            request_id, claim = task["request_id"], task["claim_token"]
            scan_body = {
                "external_request_id": str(request_id),
                "external_business_id": f"manual-{task['manual_business_id']}",
                "keyword": task["keyword"], "manual_claim_token": str(claim),
            }
            response = client.post("/api/external/scans", headers=worker, json=scan_body)
            assert response.status_code == 200, response.text
            scan = response.json()
            points = [{k: point[k] for k in ("row", "col", "lat", "lng")} for point in scan["points"]]
            assert len(points) == 49
            args = (request_id, claim, scan["scan_id"])
            with psycopg.connect(dsn) as connection:
                assert connection.execute("""
                    SELECT geogrid_prepare_tasks(%s,%s,%s,%s,%s,%s,%s)
                """, (*args, scan["business_id"], scan["center"]["lat"], scan["center"]["lng"], Jsonb(points))).fetchone()[0]
                assert connection.execute("SELECT geogrid_authorize_task_post(%s,%s,%s)", args).fetchone()[0]
                with pytest.raises(psycopg.Error), connection.transaction():
                    connection.execute("SELECT geogrid_authorize_task_post(%s,%s,%s)", args)
                post = [{**point, "task_id": f"{request_id}-{i}", "cost": ".001", "status_code": 20100}
                        for i, point in enumerate(points)]
                assert connection.execute("SELECT geogrid_save_post_response(%s,%s,%s,%s)",
                                          (request_id, claim, Jsonb(post), Decimal(".049"))).fetchone()[0] == "submitted"
            # Poll using a new connection after persisting task IDs.
            with psycopg.connect(dsn) as connection:
                polled = connection.execute("""
                    SELECT task_id,row_index,col_index,claim_token,latitude,longitude
                    FROM geogrid_claim_pending_tasks('poll-restart', 49)
                """).fetchall()
                assert len(polled) == 49
            received = [{"row": row, "col": col, "lat": lat, "lng": lng, "status": "no_results", "dataforseo_task_id": task_id}
                        for task_id, row, col, _, lat, lng in polled]
            res = client.post(f"/api/external/scans/{scan['scan_id']}/points", headers=worker, json={"points": received})
            assert res.status_code == 200, res.text
            with psycopg.connect(dsn) as connection:
                for task_id, row, col, token, _, _ in polled:
                    assert connection.execute("""
                        SELECT geogrid_record_task_result(%s,%s::smallint,%s::smallint,%s,%s,
                            'no_results',20000,NULL,NULL,NULL,'[]')
                    """, (request_id, row, col, task_id, token)).fetchone()[0]
                assert connection.execute("SELECT geogrid_ready_to_finalize(%s)", (request_id,)).fetchone()[0]
                res = client.post(f"/api/external/scans/{scan['scan_id']}/complete", headers=worker, json={})
                assert res.status_code == 200, res.text
                assert connection.execute("SELECT geogrid_finalize_request(%s)", (request_id,)).fetchone()[0] == "done"

        progress = client.get(f"{path}/{batch}").json()["researches"]
        assert len(progress) == 30
        assert all(r["status"] == "done" and r["completed_points"] == 49 for r in progress)

        rendered = []

        async def renderer(scans):
            rendered.append([scan["keyword"] for scan in scans])
            return Response(b"%PDF fixture", media_type="application/pdf")

        monkeypatch.setattr(server_reports, "basic_pdf", renderer)
        single = f"/api/external/scans/{progress[0]['scan_id']}/pdf"
        assert client.get(single).status_code == 401
        assert client.get(single, headers={**worker, "X-GeoGrid-Client-ID": "other"}).status_code == 404
        assert client.get(single, headers=worker).headers["content-type"] == "application/pdf"
        consolidated = f"{path}/{batch}/companies/{progress[0]['manual_business_id']}/pdf"
        assert client.get(consolidated).headers["content-type"] == "application/pdf"
        assert len(rendered[0]) == 1
        assert set(rendered[1]) == {"padaria", "pão integral", "café"}
        priority_key = uuid4()
        result = client.post(path, headers={"Idempotency-Key": str(priority_key)}, json={
            "modality": "priority", "approved_total_usd": ".098",
            "companies": [{
                "business_id": progress[0]["manual_business_id"], "keywords": ["priority"],
            }],
        })
        assert result.status_code == 202, result.text
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            priority_claim = connection.execute(
                "SELECT * FROM geogrid_claim_processor_request('priority-check')"
            ).fetchone()
            assert priority_claim["manual_batch_id"] == priority_key
            assert priority_claim["standard_priority"] == 2
            assert priority_claim["budget_reserved_usd"] == Decimal(".098")
            assert connection.execute("""
                SELECT geogrid_release_prepaid_reservation(%s,%s,'queued','fixture') AS released
            """, (priority_claim["request_id"], priority_claim["claim_token"])).fetchone()["released"]
            connection.execute("""
                UPDATE geogrid_budget SET estimated_priority_task_cost_usd = .003
                WHERE scope_type = 'system' AND scope_key = 'default'
            """)
            # A later price increase must never expand the operator's approved total.
            assert connection.execute(
                "SELECT request_id FROM geogrid_claim_processor_request('price-change')"
            ).fetchone() is None
            state = connection.execute(
                "SELECT status FROM geogrid_requests WHERE request_id = %s",
                (priority_claim["request_id"],),
            ).fetchone()
            assert state["status"] == "budget_blocked"
    with psycopg.connect(dsn) as connection:
        assert connection.execute("""
            SELECT count(*),count(DISTINCT t.task_id) FROM geogrid_tasks t
            JOIN geogrid_requests r USING(request_id) WHERE r.manual_batch_id = %s
        """, (batch,)).fetchone() == (1470, 1470)
        assert connection.execute("""
            SELECT spent_amount_usd,reserved_amount_usd FROM geogrid_budget
            WHERE scope_type = 'event' AND scope_key = 'manual-standard'
        """).fetchone() == (Decimal("1.47"), 0)
        assert connection.execute("""
            SELECT count(*) FROM geogrid_crm_outbox o
            JOIN geogrid_requests r USING(request_id) WHERE r.manual_batch_id = %s
        """, (batch,)).fetchone()[0] == 0
