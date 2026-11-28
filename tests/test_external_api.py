import concurrent.futures
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app import db, main


CLIENT_ID = "t2s-demo"
HEADERS = {
    "Authorization": "Bearer test-external-token",
    "X-GeoGrid-Client-ID": CLIENT_ID,
}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "external-test.db")
    monkeypatch.setenv("GEOGRID_EXTERNAL_API_TOKEN", "test-external-token")
    monkeypatch.delenv("GEOGRID_EXTERNAL_LANGUAGE_CODE", raising=False)
    monkeypatch.setenv("DATAFORSEO_LANGUAGE_CODE", "en")
    db.init_db()
    with TestClient(main.app) as client:
        yield client


def register_business(api, *, client_id=CLIENT_ID, external_business_id="landing-business-1"):
    return api.post(
        "/api/external/businesses",
        headers={**HEADERS, "X-GeoGrid-Client-ID": client_id},
        json={
            "external_business_id": external_business_id,
            "name": f"Empresa {external_business_id}",
            "cid": f"cid-{external_business_id}",
            "lat": -23.5505,
            "lng": -46.6333,
            "category": "Serviço local",
        },
    )


def create_scan(api, *, request_id="landing-request-1", business_id="landing-business-1"):
    return api.post(
        "/api/external/scans",
        headers=HEADERS,
        json={
            "external_request_id": request_id,
            "external_business_id": business_id,
            "keyword": "serviço local",
        },
    )


def point_payload(scan, row, col, *, status="ok", task_id=None, results=None, **extra):
    point = next(
        item for item in scan["points"] if item["row"] == row and item["col"] == col
    )
    return {
        "row": row,
        "col": col,
        "lat": point["lat"],
        "lng": point["lng"],
        "status": status,
        "dataforseo_task_id": task_id,
        "results": results or [],
        **extra,
    }


def target_result(scan):
    return {
        "name": "Empresa landing-business-1",
        "cid": "cid-landing-business-1",
        "category": "Serviço local",
        "rating": 4.7,
        "reviews": 123,
    }


def test_external_auth_requires_configured_and_matching_bearer(api, monkeypatch, caplog):
    monkeypatch.delenv("GEOGRID_EXTERNAL_API_TOKEN")
    disabled = api.get("/api/external/health", headers=HEADERS)
    assert disabled.status_code == 503

    monkeypatch.setenv("GEOGRID_EXTERNAL_API_TOKEN", "test-external-token")
    missing = api.get("/api/external/health")
    invalid = api.get(
        "/api/external/health",
        headers={**HEADERS, "Authorization": "Bearer wrong-token"},
    )
    assert missing.status_code == invalid.status_code == 401
    assert "test-external-token" not in caplog.text


def test_business_registration_and_scan_creation_are_fixed_and_idempotent(api):
    business = register_business(api)
    assert business.status_code == 200
    assert business.json()["created"] is True
    assert "id" not in business.json()
    repeated_business = register_business(api)
    assert repeated_business.json()["created"] is False

    first = create_scan(api)
    assert first.status_code == 200
    scan = first.json()
    assert scan["created_new"] is True
    assert scan["grid_size"] == 3
    assert scan["spacing_km"] == 1
    assert scan["language_code"] == "pt"
    assert scan["total"] == len(scan["points"]) == 9
    assert scan["status"] == "queued"
    assert scan["center"] == {"lat": -23.5505, "lng": -46.6333}
    assert len({(point["row"], point["col"]) for point in scan["points"]}) == 9

    duplicate = create_scan(api)
    assert duplicate.status_code == 200
    assert duplicate.json()["scan_id"] == scan["scan_id"]
    assert duplicate.json()["created_new"] is False
    assert db.list_scans()[0]["progress"] == 0
    conflict = api.post(
        "/api/external/scans",
        headers=HEADERS,
        json={
            "external_request_id": "landing-request-1",
            "external_business_id": "landing-business-1",
            "keyword": "outra palavra-chave",
        },
    )
    assert conflict.status_code == 409
    assert len(db.list_scans()) == 1


def test_external_tenant_scope_hides_other_clients(api):
    assert register_business(api).status_code == 200
    created = create_scan(api).json()
    other_headers = {**HEADERS, "X-GeoGrid-Client-ID": "different-client"}

    assert api.get(
        f"/api/external/scans/{created['scan_id']}", headers=other_headers
    ).status_code == 404
    assert api.post(
        f"/api/external/scans/{created['scan_id']}/complete",
        headers=other_headers,
        json={},
    ).status_code == 404
    assert api.post(
        "/api/external/scans",
        headers=other_headers,
        json={
            "external_request_id": "other-request",
            "external_business_id": "landing-business-1",
            "keyword": "serviço local",
        },
    ).status_code == 404


def test_point_ingestion_is_grid_bound_order_independent_and_idempotent(api):
    register_business(api)
    scan = create_scan(api).json()
    result = target_result(scan)
    late = point_payload(
        scan, 2, 2, task_id="task-late", results=[result]
    )
    early = point_payload(
        scan, 0, 0, task_id="task-early", results=[result]
    )

    response = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [late, early]},
    )
    assert response.status_code == 200
    assert response.json()["progress"] == 2
    assert response.json()["accepted_points"] == 2

    duplicate = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [late]},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["progress"] == 2
    assert duplicate.json()["accepted_points"] == 0
    assert duplicate.json()["idempotent"] is True

    wrong_coordinates = point_payload(scan, 1, 1, task_id="task-wrong", results=[result])
    wrong_coordinates["lat"] += 0.01
    rejected = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [wrong_coordinates]},
    )
    assert rejected.status_code == 409
    assert db.get_external_scan(CLIENT_ID, scan["scan_id"])["progress"] == 2


def test_pending_task_ids_partial_results_and_explicit_close_are_persistent(api):
    register_business(api)
    scan = create_scan(api).json()
    pending = [
        point_payload(scan, row, col, status="pending", task_id=f"task-{row}-{col}")
        for row in range(3)
        for col in range(3)
    ]
    submitted = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": pending},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "processing"
    assert submitted.json()["progress"] == 0

    ready = point_payload(
        scan, 2, 1, task_id="task-2-1", results=[target_result(scan)]
    )
    completed_point = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [ready]},
    )
    assert completed_point.json()["status"] == "partial"
    assert completed_point.json()["progress"] == 1

    not_ready = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=HEADERS,
        json={},
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["pending_points"] == 8

    close_body = {
        "allow_partial": True,
        "completion_reason": "Evento encerrado; demais tarefas canceladas",
    }
    closed = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=HEADERS,
        json=close_body,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "partial"
    assert closed.json()["finished"]
    repeated = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=HEADERS,
        json=close_body,
    )
    assert repeated.json()["idempotent"] is True

    after_restart = api.get(
        f"/api/external/scans/{scan['scan_id']}", headers=HEADERS
    )
    assert after_restart.status_code == 200
    assert after_restart.json()["status"] == "partial"
    assert after_restart.json()["points"][7]["dataforseo_task_id"] == "task-2-1"
    assert after_restart.json()["metrics"]["valid_points"] == 1
    repeated_result = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [ready]},
    )
    assert repeated_result.status_code == 200
    assert repeated_result.json()["idempotent"] is True


def test_full_scan_uses_shared_metrics_and_history(api):
    register_business(api)
    scan = create_scan(api).json()
    points = [
        point_payload(
            scan,
            row,
            col,
            task_id=f"task-{row}-{col}",
            results=[target_result(scan)],
        )
        for row in range(3)
        for col in range(3)
    ]
    saved = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": points},
    )
    assert saved.status_code == 200
    assert saved.json()["progress"] == 9

    done = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=HEADERS,
        json={},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "done"

    report = api.get(f"/api/external/scans/{scan['scan_id']}", headers=HEADERS)
    assert report.status_code == 200
    payload = report.json()
    assert payload["metrics"]["coverage_pct"] == 100
    assert payload["metrics"]["arp"] == 1
    assert payload["analysis"]["metrics"]["ivl"] is not None
    assert payload["analysis"]["icm"]["score"] is not None
    assert payload["analysis"]["icpb_v2"]["valid_points"] == 9
    assert len(payload["competitors"]) == 1
    assert payload["analysis"]["scan_id"] == scan["scan_id"]


def test_failed_external_scan_records_reason_and_startup_preserves_active_scans(api):
    register_business(api)
    scan = create_scan(api).json()
    with TestClient(main.app) as restarted:
        active = restarted.get(
            f"/api/external/scans/{scan['scan_id']}", headers=HEADERS
        ).json()
        assert active["status"] == "queued"

        pending = point_payload(
            scan, 0, 0, status="pending", task_id="persisted-task-id"
        )
        task = restarted.post(
            f"/api/external/scans/{scan['scan_id']}/points",
            headers=HEADERS,
            json={"points": [pending]},
        )
        assert task.json()["status"] == "processing"
    with TestClient(main.app) as restarted_again:
        active = restarted_again.get(
            f"/api/external/scans/{scan['scan_id']}", headers=HEADERS
        ).json()
        assert active["status"] == "processing"
        assert active["points"][0]["dataforseo_task_id"] == "persisted-task-id"

        failed = restarted_again.post(
            f"/api/external/scans/{scan['scan_id']}/complete",
            headers=HEADERS,
            json={"failure_reason": "Falha definitiva no workflow n8n"},
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["completion_reason"] == "Falha definitiva no workflow n8n"

    active = db.get_external_scan(CLIENT_ID, scan["scan_id"])
    assert active["external_status"] == "failed"
    assert active["status"] == "failed"


def test_external_api_never_invokes_live_fetcher(api, monkeypatch):
    def forbidden_fetcher():
        pytest.fail("External endpoints must not invoke the Live fetcher")

    monkeypatch.setattr(main, "fetcher", forbidden_fetcher)
    assert register_business(api).status_code == 200
    scan = create_scan(api).json()
    health = api.get("/api/external/health", headers=HEADERS)
    assert health.json() == {
        "status": "ok",
        "storage": "ok",
        "dataforseo_calls_enabled": False,
    }
    assert api.get(f"/api/external/scans/{scan['scan_id']}", headers=HEADERS).status_code == 200


def test_request_size_is_limited_before_json_parsing(api):
    response = api.post(
        "/api/external/businesses",
        headers=HEADERS,
        content=b"x" * (1_048_576 + 1),
    )
    assert response.status_code == 413


def test_concurrent_repeated_scan_request_creates_only_one_scan(api):
    register_business(api)
    payload = {
        "external_request_id": "same-request",
        "external_business_id": "landing-business-1",
        "keyword": "serviço local",
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(
                lambda _: api.post(
                    "/api/external/scans",
                    headers=HEADERS,
                    json=payload,
                ),
                range(8),
            )
        )

    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["scan_id"] for response in responses}) == 1
    assert sum(response.json()["created_new"] for response in responses) == 1
    assert len(db.list_scans()) == 1


def test_external_scan_input_rejects_nonfixed_grid_and_duplicate_cells(api):
    register_business(api)
    invalid_grid = api.post(
        "/api/external/scans",
        headers=HEADERS,
        json={
            "external_request_id": "bad-grid",
            "external_business_id": "landing-business-1",
            "keyword": "serviço local",
            "grid_size": 5,
        },
    )
    assert invalid_grid.status_code == 422

    scan = create_scan(api).json()
    duplicated_cell = point_payload(
        scan, 0, 0, task_id="duplicate-a", results=[target_result(scan)]
    )
    second_copy = {**duplicated_cell, "dataforseo_task_id": "duplicate-b"}
    rejected = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": [duplicated_cell, second_copy]},
    )
    assert rejected.status_code == 422


def test_generated_grid_rejects_invalid_coordinates_near_pole(api):
    polar_business = api.post(
        "/api/external/businesses",
        headers=HEADERS,
        json={
            "external_business_id": "polar-business",
            "name": "Polar business",
            "lat": 90,
            "lng": 0,
        },
    )
    assert polar_business.status_code == 200
    scan = api.post(
        "/api/external/scans",
        headers=HEADERS,
        json={
            "external_request_id": "polar-request",
            "external_business_id": "polar-business",
            "keyword": "serviço local",
        },
    )
    assert scan.status_code == 422


def test_no_results_and_errors_remain_distinct_from_rank_20(api):
    register_business(api)
    scan = create_scan(api).json()
    submitted = [
        point_payload(
            scan,
            0,
            0,
            status="no_results",
            task_id="no-results-task",
            api_status_code=40102,
            api_status_message="No Search Results",
        ),
        point_payload(
            scan,
            0,
            1,
            status="error",
            task_id="failed-task",
            error="Provider unavailable",
            error_category="technical_error",
            api_status_code=50301,
            api_status_message="3rd party api service unavailable",
        ),
        point_payload(
            scan,
            0,
            2,
            task_id="valid-task",
            results=[{"name": "Concorrente", "cid": "competitor-cid"}],
        ),
    ]
    response = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": submitted},
    )
    assert response.status_code == 200
    assert response.json()["progress"] == 3

    completed = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=HEADERS,
        json={"allow_partial": True, "completion_reason": "No remaining results"},
    )
    assert completed.json()["status"] == "partial"
    report = api.get(
        f"/api/external/scans/{scan['scan_id']}", headers=HEADERS
    ).json()
    assert report["points"][0]["status"] == "no_results"
    assert report["points"][0]["rank"] is None
    assert report["points"][0]["api_status_code"] == 40102
    assert report["points"][1]["status"] == "error"
    assert report["points"][1]["rank"] is None
    assert report["coverage"]["valid_points"] == 1
    assert report["coverage"]["no_results_points"] == 1
    assert report["coverage"]["failed_points"] == 1
    assert report["coverage"]["pending_points"] == 6


def test_duplicate_dataforseo_task_id_cannot_be_assigned_to_two_points(api):
    register_business(api)
    scan = create_scan(api).json()
    same_task = [
        point_payload(
            scan, row, col, task_id="shared-task", results=[target_result(scan)]
        )
        for row, col in [(0, 0), (0, 1)]
    ]
    response = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=HEADERS,
        json={"points": same_task},
    )
    assert response.status_code == 409
    assert db.get_external_scan(CLIENT_ID, scan["scan_id"])["progress"] == 0


def test_legacy_scan_route_fails_closed_for_dataforseo(api, monkeypatch):
    business_id = db.upsert_business(
        {
            "name": "Legacy route security test",
            "cid": "legacy-route-security-test",
            "lat": -23.5505,
            "lng": -46.6333,
        }
    )
    monkeypatch.setenv("GEOGRID_FETCHER", "dataforseo")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "test-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "test-password")
    for approval in (
        "ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED",
        "ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED",
        "ZURI_GOOGLE_UPSTREAM_USE_APPROVED",
        "GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED",
    ):
        monkeypatch.setenv(approval, "true")

    create_scans = Mock(
        side_effect=AssertionError("The route must reject before creating a scan")
    )
    run_queued = AsyncMock(
        side_effect=AssertionError("The route must reject before scheduling execution")
    )
    monkeypatch.setattr(main, "create_scans", create_scans)
    monkeypatch.setattr(main, "_run_queued", run_queued)
    request = {
        "business_id": business_id,
        "keywords": ["serviço local"],
        "grid_size": 3,
        "spacing_km": 1,
    }

    for headers in (
        {},
        {"Authorization": "Bearer invalid-token"},
        {"Authorization": "Bearer test-external-token"},
    ):
        response = api.post("/api/scans", json=request, headers=headers)
        assert response.status_code == 403

    create_scans.assert_not_called()
    run_queued.assert_not_called()
    assert db.list_scans(business_id) == []


def test_legacy_scan_route_keeps_free_playwright_flow(api, monkeypatch):
    business_id = db.upsert_business(
        {
            "name": "Legacy route free-flow test",
            "cid": "legacy-route-free-flow-test",
            "lat": -23.5505,
            "lng": -46.6333,
        }
    )
    monkeypatch.setenv("GEOGRID_FETCHER", "playwright")

    monkeypatch.setattr(main, "_run_queued", AsyncMock(return_value=None))
    response = api.post(
        "/api/scans",
        json={
            "business_id": business_id,
            "keywords": ["serviço local"],
            "grid_size": 3,
            "spacing_km": 1,
        },
    )

    assert response.status_code == 200
    assert len(response.json()["scan_ids"]) == 1
    assert len(db.list_scans(business_id)) == 1
