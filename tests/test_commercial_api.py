import asyncio
import csv
import hashlib
import io
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app import business_finder, commercial_api, commercial_store, prospecting
from app.grid import GridPoint


SIGNING_SECRET = "commercial-simulator-signing-secret-32-bytes"
OPERATOR_TOKEN = "commercial-simulator-operator-token-32-bytes"
WORDPRESS_TOKEN = "commercial-simulator-wordpress-token-32-bytes"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "commercial-test.sqlite")
    for name, value in {
        "GEOGRID_BUSINESS_FINDER_SIGNING_SECRET": SIGNING_SECRET,
        "GEOGRID_BUSINESS_FINDER_PROVIDER": "simulator",
        "ZURI_COMMERCIAL_SIMULATOR_ENABLED": "true",
        "GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT": "100",
        "GEOGRID_BUSINESS_FINDER_RATE_LIMIT_PER_MINUTE": "100",
        "ZURI_LEADS_RATE_LIMIT": "1000",
        "ZURI_STATUS_RATE_LIMIT": "1000",
        "ZURI_REPORT_RATE_LIMIT": "1000",
        "ZURI_ALLOWED_CAMPAIGNS": "sim-event",
        "ZURI_CAMPAIGN_ID": "sim-event",
        "ZURI_PRIVACY_POLICY_URL": "https://zuri.example.test/privacy",
        "ZURI_PRIVACY_POLICY_VERSION": "test-1",
        "GEOGRID_COMMERCE_OPERATOR_TOKEN": OPERATOR_TOKEN,
        "GEOGRID_COMMERCE_OPERATOR_IDS": "operator.test",
        "GEOGRID_EXTERNAL_API_TOKEN": "test-external-token",
        "ZURI_COMMERCE_ADMIN_ALLOW_INSECURE_HTTP": "true",
        "ZURI_COMMERCE_ADMIN_COOKIE_SECURE": "false",
        "GEOGRID_WORDPRESS_API_TOKEN": WORDPRESS_TOKEN,
        "ZURI_COMMERCIAL_DAILY_DIAGNOSTICS_LIMIT": "20",
        "ZURI_COMMERCIAL_DAILY_POINTS_LIMIT": "1000",
        "ZURI_COMMERCIAL_PENDING_DIAGNOSTICS_LIMIT": "10",
        "ZURI_COMMERCIAL_PENDING_POINTS_LIMIT": "500",
    }.items():
        monkeypatch.setenv(name, value)
    db.init_db()
    with TestClient(main.app) as client:
        yield client


def _search(api, **changes):
    payload = {
        "request_id": str(uuid.uuid4()),
        "business_name": "Café Lume",
        "city": "São Paulo",
        "state": "SP",
    }
    payload.update(changes)
    response = api.post("/api/business-finder/search", json=payload)
    assert response.status_code == 200
    return payload, response.json()


def _confirmed_flow(api):
    search, results = _search(api)
    assert results["classification"] == "BUSINESS_AMBIGUOUS"
    candidate = results["candidates"][0]
    confirmation = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": str(uuid.uuid4()),
            "candidate_token": candidate["candidate_token"],
            "confirmed": True,
        },
    )
    assert confirmation.status_code == 200

    status_token = uuid.uuid4().hex * 2
    lead_payload = {
        "request_id": str(uuid.uuid4()),
        "status_token": status_token,
        "business_confirmed": True,
        "confirmation_token": confirmation.json()["confirmation_token"],
        "search_request_id": search["request_id"],
        "business_search": search,
        "business_name": "Café Lume - Unidade Centro",
        "city": search["city"],
        "state": search["state"],
        "address": None,
        "google_maps_url": None,
        "contact_name": "Ana Exemplo",
        "email": "ana@example.com",
        "whatsapp": "+55 11 99999-9999",
        "campaign_id": "sim-event",
        "privacy_acknowledged": True,
        "marketing_consent": False,
    }
    lead = api.post("/api/commercial/leads", json=lead_payload)
    assert lead.status_code == 201
    return search, candidate, confirmation.json()["confirmation_token"], lead.json()


def _start_express(api):
    search, _, confirmation_token, lead = _confirmed_flow(api)
    payload = {
        "request_id": str(uuid.uuid4()),
        "lead_id": lead["lead_id"],
        "status_token": lead["status_token"],
        "product_id": "express",
        "confirmation_token": confirmation_token,
        "search": search,
        "keyword": "cafeteria em São Paulo",
        "confirmed": True,
    }
    response = api.post("/api/commercial/diagnostics", json=payload)
    assert response.status_code == 202
    return payload, response.json(), lead


def _wait_for_status(api, lead, product_id):
    for _ in range(50):
        response = api.post(
            f"/api/commercial/leads/{lead['lead_id']}/status",
            json={
                "lead_id": lead["lead_id"],
                "status_token": lead["status_token"],
            },
        )
        assert response.status_code == 200
        matching = [
            item
            for item in response.json()["diagnostics"]
            if item["product_id"] == product_id
        ]
        if matching and matching[-1]["status"] in {"done", "failed"}:
            return matching[-1]
        time.sleep(0.02)
    pytest.fail(f"{product_id} simulated diagnostic did not finish")


def test_simulated_business_to_professional_order_flow_is_idempotent(api):
    diagnostic_payload, started, lead = _start_express(api)
    express = started["diagnostic"]
    _wait_for_status(api, lead, "express")

    duplicate = api.post("/api/commercial/diagnostics", json=diagnostic_payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["diagnostic"]["diagnostic_id"] == express["diagnostic_id"]

    report_response = api.post(
        f"/api/commercial/diagnostics/{express['diagnostic_id']}/report",
        json={"lead_id": lead["lead_id"], "status_token": lead["status_token"]},
    )
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["report_type"] == "express"
    assert report["grid_size"] == 3
    with db.connect() as connection:
        saved_scan = connection.execute(
            "SELECT s.id, s.grid_size, s.total, s.progress, s.origin, COUNT(p.id) AS points "
            "FROM commercial_diagnostics d JOIN scans s ON s.id = d.scan_id "
            "LEFT JOIN points p ON p.scan_id = s.id WHERE d.id = ? GROUP BY s.id",
            (express["diagnostic_id"],),
        ).fetchone()
    assert saved_scan["total"] == 9
    assert saved_scan["progress"] == 9
    assert saved_scan["points"] == 9
    assert saved_scan["origin"] == "business_finder"
    assert report["coverage"]["requested_points"] == 9, report["coverage"]
    assert report["coverage"]["coverage_pct"] == 100
    assert report["data_mode"] == "synthetic"
    assert report["offer"]["checkout_url"] is None

    order_payload = {
        "request_id": str(uuid.uuid4()),
        "lead_id": lead["lead_id"],
        "status_token": lead["status_token"],
        "source_diagnostic_id": express["diagnostic_id"],
        "product_id": "professional",
    }
    order_response = api.post("/api/commercial/orders", json=order_payload)
    assert order_response.status_code == 201
    order = order_response.json()
    assert order["amount_brl"] == "29.90"
    assert order["checkout_url"] is None
    assert order["checkout_is_payment_proof"] is False

    headers = {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "X-Operator-ID": "operator.test",
    }
    confirm_url = f"/api/commercial/operator/orders/{order['order_id']}/confirm-payment"
    confirmation = api.post(
        confirm_url,
        headers=headers,
        json={"payment_reference": "SIM-PAYMENT-001"},
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["status"] == "payment_confirmed"

    release_url = f"/api/commercial/operator/orders/{order['order_id']}/release"
    released = api.post(release_url, headers=headers)
    assert released.status_code == 200
    professional = released.json()["diagnostic"]
    assert professional["grid_size"] == 7
    assert professional["total"] == 49
    duplicate_release = api.post(release_url, headers=headers)
    assert duplicate_release.status_code == 200
    assert duplicate_release.json()["diagnostic"]["diagnostic_id"] == professional["diagnostic_id"]
    with db.connect() as connection:
        assert connection.execute(
            "SELECT s.origin FROM scans s JOIN commercial_diagnostics d ON d.scan_id = s.id "
            "WHERE d.id = ?",
            (professional["diagnostic_id"],),
        ).fetchone()["origin"] == "commercial"
    _wait_for_status(api, lead, "professional")

    professional_report = api.post(
        f"/api/commercial/diagnostics/{professional['diagnostic_id']}/report",
        json={"lead_id": lead["lead_id"], "status_token": lead["status_token"]},
    )
    assert professional_report.status_code == 200
    assert professional_report.json()["report_type"] == "professional"
    assert professional_report.json()["coverage"]["requested_points"] == 49

    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 58
        assert connection.execute("SELECT COUNT(*) FROM commercial_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM commercial_order_events").fetchone()[0] == 3


def test_unconfirmed_or_assisted_lead_cannot_start_diagnostic(api):
    lead = api.post(
        "/api/commercial/leads",
        json={
            "request_id": str(uuid.uuid4()),
            "status_token": "b" * 64,
            "business_confirmed": False,
            "business_name": "Empresa Não Localizada",
            "city": "Curitiba",
            "state": "PR",
            "contact_name": "Caio Exemplo",
            "email": "caio@example.com",
            "whatsapp": "+55 41 99999-9999",
            "campaign_id": "sim-event",
            "privacy_acknowledged": True,
            "marketing_consent": False,
        },
    )
    assert lead.status_code == 201
    search, _ = _search(
        api,
        business_name="Empresa Não Localizada",
        city="Curitiba",
        state="PR",
    )
    denied = api.post(
        "/api/commercial/diagnostics",
        json={
            "request_id": str(uuid.uuid4()),
            "lead_id": lead.json()["lead_id"],
            "status_token": lead.json()["status_token"],
            "product_id": "express",
            "confirmation_token": "not-a-valid-confirmation-token-123456",
            "search": search,
            "keyword": "teste local",
            "confirmed": True,
        },
    )
    assert denied.status_code == 409
    assert "confirmed business" in denied.json()["detail"]
    assert db.list_scans() == []


def test_professional_release_requires_operator_and_respects_point_budget(api, monkeypatch):
    _, started, lead = _start_express(api)
    _wait_for_status(api, lead, "express")
    express = started["diagnostic"]
    order = api.post(
        "/api/commercial/orders",
        json={
            "request_id": str(uuid.uuid4()),
            "lead_id": lead["lead_id"],
            "status_token": lead["status_token"],
            "source_diagnostic_id": express["diagnostic_id"],
            "product_id": "professional",
        },
    ).json()
    release_url = f"/api/commercial/operator/orders/{order['order_id']}/release"
    operator_headers = {
        "Authorization": f"Bearer {OPERATOR_TOKEN}",
        "X-Operator-ID": "operator.test",
    }

    before_payment = api.post(release_url, headers=operator_headers)
    assert before_payment.status_code == 409
    unauthorized = api.post(
        f"/api/commercial/operator/orders/{order['order_id']}/confirm-payment",
        headers={
            "Authorization": f"Bearer {OPERATOR_TOKEN}",
            "X-Operator-ID": "unlisted.operator",
        },
        json={"payment_reference": "SIM-PAYMENT-002"},
    )
    assert unauthorized.status_code == 403

    confirmation = api.post(
        f"/api/commercial/operator/orders/{order['order_id']}/confirm-payment",
        headers=operator_headers,
        json={"payment_reference": "SIM-PAYMENT-002"},
    )
    assert confirmation.status_code == 200
    monkeypatch.setenv("ZURI_COMMERCIAL_DAILY_POINTS_LIMIT", "9")
    over_budget = api.post(release_url, headers=operator_headers)
    assert over_budget.status_code == 429
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1


def test_worker_never_resubmits_partial_or_finalized_scans(api, monkeypatch):
    assert api.get("/").status_code == 200
    business_id = db.upsert_business(
        {
            "name": "Simulated business",
            "cid": "simulated-cid",
            "lat": -23.55,
            "lng": -46.63,
        }
    )
    partial_scan_id = db.create_scan(
        business_id, "keyword", 3, 1.0, -23.55, -46.63
    )
    db.save_point(
        partial_scan_id,
        GridPoint(0, 0, -23.55, -46.63),
        None,
        "failed",
        None,
        "simulated interruption",
    )
    finalized_scan_id = db.create_scan(
        business_id, "keyword", 3, 1.0, -23.55, -46.63
    )
    db.finish_scan(finalized_scan_id, "done", {}, [])

    async def unexpected_resubmission(*args, **kwargs):
        pytest.fail(f"unexpected resubmission: {args!r} {kwargs!r}")

    monkeypatch.setattr(main, "run_scan", unexpected_resubmission)
    asyncio.run(
        main._run_queued(
            [partial_scan_id, finalized_scan_id],
            simulated=True,
        )
    )

    partial = db.get_scan(partial_scan_id, with_points=False)
    finalized = db.get_scan(finalized_scan_id, with_points=False)
    assert partial["status"] == "failed"
    assert partial["error"] == "partial scan was not automatically resubmitted"
    assert finalized["status"] == "done"


def test_wordpress_contract_requires_server_bearer_and_simulator(api, monkeypatch):
    body = {
        "request_id": str(uuid.uuid4()),
        "business_name": "Oficina Horizonte",
        "city": "Curitiba",
        "state": "PR",
    }
    unauthorized = api.post("/api/wordpress/v1/businesses/search", json=body)
    assert unauthorized.status_code == 401

    headers = {"Authorization": f"Bearer {WORDPRESS_TOKEN}"}
    found = api.post(
        "/api/wordpress/v1/businesses/search",
        headers=headers,
        json=body,
    )
    assert found.status_code == 200
    assert found.json()["classification"] == "BUSINESS_LOCATED"

    monkeypatch.setenv("ZURI_COMMERCIAL_SIMULATOR_ENABLED", "false")
    blocked = api.post(
        "/api/wordpress/v1/businesses/search",
        headers=headers,
        json={**body, "request_id": str(uuid.uuid4())},
    )
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["category"] == "SIMULATOR_DISABLED"


def _diagnostic_scan_id(diagnostic_id):
    diagnostic = commercial_store.get_diagnostic(diagnostic_id)
    assert diagnostic and diagnostic["scan_id"]
    return diagnostic["scan_id"]


def test_express_captures_deduplicated_synthetic_prospects_without_export(api):
    _, first_started, first_lead = _start_express(api)
    _wait_for_status(api, first_lead, "express")
    first_scan_id = _diagnostic_scan_id(
        first_started["diagnostic"]["diagnostic_id"]
    )
    first_capture = prospecting.capture_completed_scan(first_scan_id)
    assert first_capture["captured"] == 0
    assert first_capture["data_mode"] == "synthetic"
    first_rows = prospecting.list_prospects(provider="simulator")
    assert first_rows
    assert all(row["data_mode"] == "synthetic" for row in first_rows)
    assert all(row["export_eligible"] == 0 for row in first_rows)
    assert all(row["qualification"] == "simulation" for row in first_rows)
    assert {row["record_status"] for row in first_rows} == {"new"}
    with db.connect() as connection:
        original_observation_count = connection.execute(
            "SELECT COUNT(*) FROM commercial_prospect_observations"
        ).fetchone()[0]

    _, second_started, second_lead = _start_express(api)
    _wait_for_status(api, second_lead, "express")
    second_capture = prospecting.capture_completed_scan(
        _diagnostic_scan_id(second_started["diagnostic"]["diagnostic_id"])
    )
    assert second_capture["captured"] == 0
    assert second_capture["new_candidates"] == 0
    assert second_capture["previously_known"] == second_capture["unique_candidates"]
    refreshed_rows = prospecting.list_prospects(provider="simulator")
    assert len(refreshed_rows) == len(first_rows)
    assert {row["record_status"] for row in refreshed_rows} == {"previously_known"}
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM commercial_prospect_observations"
        ).fetchone()[0] == original_observation_count * 2
    assert prospecting.exportable_prospects() == []


def test_commercial_admin_authenticates_and_exports_only_consented_leads(api):
    def create_lead(status_token, contact_name, email, marketing_consent):
        response = api.post(
            "/api/commercial/leads",
            json={
                "request_id": str(uuid.uuid4()),
                "status_token": status_token,
                "business_confirmed": False,
                "business_name": '=HYPERLINK("https://example.test")',
                "city": "Curitiba",
                "state": "PR",
                "contact_name": contact_name,
                "email": email,
                "whatsapp": "+55 41 99999-9999",
                "campaign_id": "sim-event",
                "privacy_acknowledged": True,
                "marketing_consent": marketing_consent,
            },
        )
        assert response.status_code == 201

    create_lead("c" * 64, "=Contato", "consent@example.com", True)
    create_lead("d" * 64, "Sem Consentimento", "private@example.com", False)
    assert api.get("/api/commercial/admin/leads").status_code == 401
    login = api.post(
        "/api/commercial/admin/login",
        json={"operator_id": "operator.test", "operator_token": OPERATOR_TOKEN},
    )
    assert login.status_code == 200
    assert "zuri_commerce_admin" in api.cookies
    leads = api.get("/api/commercial/admin/leads")
    assert leads.status_code == 200
    assert len(leads.json()["items"]) == 2
    exported = api.get("/api/commercial/admin/export/leads.csv")
    assert exported.status_code == 200
    assert "consent@example.com" in exported.text
    assert "private@example.com" not in exported.text
    assert "'=HYPERLINK" in exported.text
    blocked = api.get("/api/commercial/admin/export/prospects.csv")
    assert blocked.status_code == 403
    logout = api.post("/api/commercial/admin/logout", json={})
    assert logout.status_code == 204
    assert api.get("/api/commercial/admin/leads").status_code == 401


def test_real_express_is_closed_without_all_explicit_gates(api, monkeypatch):
    payload, _, _ = _start_express(api)
    monkeypatch.setattr(commercial_api, "simulator_ready", lambda: False)
    response = api.post("/api/commercial/diagnostics", json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["category"] == "REAL_EXPRESS_DISABLED"
    assert "google_coordinate_storage_not_approved" in response.json()["detail"]["blockers"]


def test_real_express_uses_confirmed_place_and_idempotent_n8n_request(api, monkeypatch):
    monkeypatch.setattr(commercial_api, "simulator_ready", lambda: False)
    for name in (
        "ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED",
        "ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED",
        "ZURI_GOOGLE_UPSTREAM_USE_APPROVED",
        "GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED",
        "ZURI_N8N_EXPRESS_PIPELINE_ENABLED",
        "ZURI_GOOGLE_COORDINATE_STORAGE_APPROVED",
        "ZURI_PRODUCT_EXPRESS_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv(
        "ZURI_N8N_LEADS_WEBHOOK_URL",
        "https://n8n.example.test/webhook/express",
    )

    search = {
        "request_id": str(uuid.uuid4()),
        "business_name": "Café Lume",
        "city": "São Paulo",
        "state": "SP",
    }
    query = business_finder._query_from_input(
        business_finder.BusinessSearchIn(**search)
    )
    nonce = "confirmed-real-express-nonce"
    nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
    db.create_business_finder_confirmation(
        nonce_hash,
        int(time.time()) + 600,
    )
    confirmation_token = business_finder._token(
        {
            "purpose": "business_finder",
            "provider_id": "google_places",
            "reference_id": "ChIJConfirmedPlace123",
            "search_request_id": search["request_id"],
            "query_hash": business_finder._query_hash(
                query,
                SIGNING_SECRET.encode(),
            ),
            "nonce": nonce,
            "stage": "confirmed",
            "expires_at": int(time.time()) + 600,
        },
        SIGNING_SECRET.encode(),
    )
    status_token = "e" * 64
    lead_response = api.post(
        "/api/commercial/leads",
        json={
            "request_id": str(uuid.uuid4()),
            "status_token": status_token,
            "business_confirmed": True,
            "confirmation_token": confirmation_token,
            "search_request_id": search["request_id"],
            "business_search": search,
            "business_name": "Café Lume",
            "city": "São Paulo",
            "state": "SP",
            "contact_name": "Ana Exemplo",
            "email": "ana@example.com",
            "whatsapp": "+55 11 99999-9999",
            "campaign_id": "sim-event",
            "privacy_acknowledged": True,
            "marketing_consent": False,
        },
    )
    assert lead_response.status_code == 201

    submitted = []

    async def accept_n8n(lead, request):
        submitted.append((lead, request))
        return {"accepted": True, "status": "queued"}

    monkeypatch.setattr(commercial_api, "submit_landing_lead", accept_n8n)
    diagnostic_payload = {
        "request_id": str(uuid.uuid4()),
        "lead_id": lead_response.json()["lead_id"],
        "status_token": status_token,
        "product_id": "express",
        "confirmation_token": confirmation_token,
        "search": search,
        "keyword": "cafeteria em São Paulo",
        "confirmed": True,
    }
    response = api.post("/api/commercial/diagnostics", json=diagnostic_payload)
    assert response.status_code == 202
    assert response.json()["data_mode"] == "standard_normal"
    assert response.json()["queued_via"] == "n8n"
    assert response.json()["diagnostic"]["status"] == "queued"
    assert submitted[0][0].google_maps_url.endswith(
        "query_place_id=ChIJConfirmedPlace123"
    )
    assert submitted[0][0].keyword == diagnostic_payload["keyword"]
    assert submitted[0][0].status_token == status_token
    repeated = api.post(
        "/api/commercial/diagnostics",
        json=diagnostic_payload,
    )
    assert repeated.status_code == 202
    assert repeated.json()["duplicate"] is True
    assert len(submitted) == 1
    with db.connect() as connection:
        consumed = connection.execute(
            "SELECT request_id FROM business_finder_confirmations WHERE nonce_hash = ?",
            (nonce_hash,),
        ).fetchone()
    assert consumed["request_id"] == diagnostic_payload["request_id"]

    external_headers = {
        "Authorization": "Bearer test-external-token",
        "X-GeoGrid-Client-ID": "t2s-demo",
    }
    business_response = api.post(
        "/api/external/businesses",
        headers=external_headers,
        json={
            "external_business_id": "confirmed-google-place",
            "name": "Café Lume",
            "place_id": "ChIJConfirmedPlace123",
            "lat": -23.5505,
            "lng": -46.6333,
            "address": "Rua Fictícia, São Paulo",
            "category": "cafeteria",
        },
    )
    assert business_response.status_code == 200
    scan_response = api.post(
        "/api/external/scans",
        headers=external_headers,
        json={
            "external_request_id": diagnostic_payload["request_id"],
            "external_business_id": "confirmed-google-place",
            "keyword": diagnostic_payload["keyword"],
        },
    )
    assert scan_response.status_code == 200
    scan = scan_response.json()
    assert scan["grid_size"] == 3
    assert scan["spacing_km"] == 1
    assert scan["total"] == 9
    assert commercial_store.get_diagnostic(
        response.json()["diagnostic"]["diagnostic_id"]
    )["scan_id"] == db.get_external_scan(
        "t2s-demo", scan["scan_id"]
    )["id"]

    competitor = {
        "name": "Concorrente Fictício",
        "cid": "mock-competitor-cid",
        "phone": "+55 11 99999-9999",
        "url": "https://concorrente.example.br",
        "position": 2,
        "address": "Rua Fictícia, 10, São Paulo",
        "category": "cafeteria",
    }
    points = []
    for point in scan["points"]:
        points.append(
            {
                "row": point["row"],
                "col": point["col"],
                "lat": point["lat"],
                "lng": point["lng"],
                "status": "ok",
                "results": [
                    {
                        "name": "Café Lume",
                        "place_id": "ChIJConfirmedPlace123",
                        "lat": -23.5505,
                        "lng": -46.6333,
                        "address": "Rua Fictícia, São Paulo",
                    },
                    competitor,
                ],
            }
        )
    point_response = api.post(
        f"/api/external/scans/{scan['scan_id']}/points",
        headers=external_headers,
        json={"points": points},
    )
    assert point_response.status_code == 200
    monkeypatch.setenv("ZURI_PROSPECTING_DATA_USE_APPROVED", "true")
    completed = api.post(
        f"/api/external/scans/{scan['scan_id']}/complete",
        headers=external_headers,
        json={},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert completed.json()["opportunity_capture"]["new_candidates"] == 1
    assert completed.json()["opportunity_capture"]["export_eligible"] is True
    prospects = prospecting.list_prospects(
        provider="dataforseo_standard",
        export_eligible=True,
    )
    assert len(prospects) == 1
    assert prospects[0]["name"] == "Concorrente Fictício"
    assert prospects[0]["phone"] == "+55 11 99999-9999"
    assert prospects[0]["website"] == "https://concorrente.example.br"
    assert prospects[0]["record_status"] == "new"
    positions = json.loads(prospects[0]["provider_positions_by_point"])
    assert len(positions) == 9
    assert {item["position"] for item in positions} == {2}
    external_scan = db.get_external_scan("t2s-demo", scan["scan_id"])
    assert external_scan is not None
    stored_scan = db.get_scan(external_scan["id"])
    stored_target = stored_scan["points"][0]["results"][0]
    assert stored_target["phone"] is None
    assert stored_target["url"] is None
    assert len(prospecting.exportable_prospects()) == 1
    login = api.post(
        "/api/commercial/admin/login",
        json={"operator_id": "operator.test", "operator_token": OPERATOR_TOKEN},
    )
    assert login.status_code == 200
    exported_csv = api.get("/api/commercial/admin/export/prospects.csv")
    assert exported_csv.status_code == 200
    assert "+55 11 99999-9999" in exported_csv.text
    assert "https://concorrente.example.br" in exported_csv.text
    assert "best_rank_in_any_point" in exported_csv.text
    csv_rows = list(csv.DictReader(io.StringIO(exported_csv.content.decode("utf-8-sig"))))
    assert len(json.loads(csv_rows[0]["provider_positions_by_point"])) == 9

    order_payload = {
        "request_id": str(uuid.uuid4()),
        "lead_id": lead_response.json()["lead_id"],
        "status_token": status_token,
        "source_diagnostic_id": response.json()["diagnostic"]["diagnostic_id"],
        "product_id": "professional",
    }
    order_response = api.post("/api/commercial/orders", json=order_payload)
    assert order_response.status_code == 201
    assert order_response.json()["status"] == "assisted"
    assert order_response.json()["fulfillment"] == "assisted"
    assert order_response.json()["checkout_url"] is None
    repeated_order = api.post("/api/commercial/orders", json=order_payload)
    assert repeated_order.status_code == 201
    assert repeated_order.json()["duplicate"] is True
    payment_response = api.post(
        f"/api/commercial/operator/orders/{order_response.json()['order_id']}/confirm-payment",
        headers={
            "Authorization": f"Bearer {OPERATOR_TOKEN}",
            "X-Operator-ID": "operator.test",
        },
        json={"payment_reference": "simulated-reference"},
    )
    assert payment_response.status_code == 503
    assert payment_response.json()["detail"]["category"] == "SIMULATOR_DISABLED"
