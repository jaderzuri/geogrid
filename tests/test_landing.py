import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import landing, main


class MockN8nResponse:
    def __init__(self, value, status_code=202, headers=None):
        self.value = value
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.value


class MockN8nClient:
    calls = []
    timeouts = []
    response_factory = None

    def __init__(self, **kwargs):
        type(self).timeouts.append(kwargs["timeout"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        self.calls.append((url, json))
        return type(self).response_factory(url, json)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOGRID_DB", str(tmp_path / "landing-test.sqlite"))
    monkeypatch.setenv("ZURI_N8N_LEADS_WEBHOOK_URL", "https://n8n.example.test/webhook/leads")
    monkeypatch.setenv(
        "ZURI_N8N_STATUS_WEBHOOK_URL",
        "https://n8n.example.test/webhook/status",
    )
    monkeypatch.setenv(
        "ZURI_N8N_REPORT_WEBHOOK_URL",
        "https://n8n.example.test/webhook/report",
    )
    monkeypatch.setenv("ZURI_CAMPAIGN_ID", "event-test")
    monkeypatch.setenv("ZURI_PRIVACY_POLICY_URL", "https://zuri.example.test/privacy")
    monkeypatch.setenv("ZURI_PRIVACY_POLICY_VERSION", "2026-11")
    monkeypatch.setenv("ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED", "true")
    monkeypatch.setenv("ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED", "true")
    monkeypatch.setenv("ZURI_GOOGLE_UPSTREAM_USE_APPROVED", "true")
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "maptiler")
    monkeypatch.setenv("GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED", "true")
    monkeypatch.setenv("ZURI_LEADS_RATE_LIMIT", "1000")
    monkeypatch.setenv("ZURI_STATUS_RATE_LIMIT", "1000")
    monkeypatch.setenv("ZURI_REPORT_RATE_LIMIT", "1000")
    monkeypatch.setattr(landing.httpx, "AsyncClient", MockN8nClient)
    MockN8nClient.calls = []
    MockN8nClient.timeouts = []
    MockN8nClient.response_factory = lambda url, payload: MockN8nResponse(
        {
            "accepted": True,
            "request_id": payload.get("request_id", str(uuid.uuid4())),
            "status": "queued",
            "duplicate": False,
        }
    )


def _payload(**overrides):
    body = {
        "request_id": str(uuid.uuid4()),
        "status_token": "a" * 64,
        "contact_name": "Ana Exemplo",
        "company_name": "Empresa Exemplo",
        "whatsapp": "+55 11 99999-9999",
        "email": "ana@example.com",
        "city": "São Paulo",
        "state": "SP",
        "google_maps_url": None,
        "keyword": "clínica odontológica",
        "campaign_id": "event-test",
        "privacy_acknowledged": True,
        "marketing_consent": False,
        "website": "",
    }
    body.update(overrides)
    return body


@pytest.fixture
def client(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    with TestClient(main.app) as test_client:
        yield test_client


def test_landing_pages_are_responsive_assets_and_privacy_headers_are_set(client):
    page = client.get("/landing")
    assert page.status_code == 200
    assert 'lang="pt-BR"' in page.text
    assert "marketing_consent" in page.text
    assert "Google Perfil da Empresa" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

    diagnostic = client.get("/diagnostico")
    assert diagnostic.status_code == 200
    assert "/static/diagnostic.js" in diagnostic.text


def test_public_config_requires_campaign_webhook_and_policy(client):
    config = client.get("/api/landing/config").json()
    assert config == {
        "enabled": True,
        "campaign_id": "event-test",
        "privacy_policy_url": "https://zuri.example.test/privacy",
        "privacy_policy_version": "2026-11",
        "commercial_diagnostics_enabled": True,
        "commercial_diagnostics_blockers": [],
        "products": [
            {
                "id": "express",
                "name": "Express",
                "price_brl": "0.00",
                "billing": "one_time",
                "grid_size": 3,
                "keywords": 1,
                "assisted": False,
                "available": False,
                "checkout_url": None,
            },
            {
                "id": "professional",
                "name": "Professional",
                "price_brl": "29.90",
                "billing": "one_time",
                "grid_size": 7,
                "keywords": 1,
                "assisted": False,
                "available": False,
                "checkout_url": None,
            },
            {
                "id": "monitor",
                "name": "Monitor",
                "price_brl": "97.00",
                "billing": "monthly_assisted",
                "grid_size": None,
                "keywords": 1,
                "assisted": True,
                "available": False,
                "checkout_url": None,
            },
        ],
    }


@pytest.mark.parametrize(
    ("variable", "blocker"),
    [
        ("ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED", "commercial_diagnostics_disabled"),
        ("ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED", "dataforseo_use_not_approved"),
        ("ZURI_GOOGLE_UPSTREAM_USE_APPROVED", "google_upstream_use_not_approved"),
        (
            "GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED",
            "maptiler_commercial_plan_unconfirmed",
        ),
    ],
)
def test_commercial_landing_is_disabled_without_explicit_provider_approvals(
    client, monkeypatch, variable, blocker
):
    monkeypatch.delenv(variable)

    config = client.get("/api/landing/config").json()

    assert config["enabled"] is False
    assert config["commercial_diagnostics_enabled"] is False
    assert blocker in config["commercial_diagnostics_blockers"]


def test_lead_submission_fails_closed_before_n8n_when_commercial_gate_is_incomplete(
    client, monkeypatch
):
    monkeypatch.delenv("ZURI_GOOGLE_UPSTREAM_USE_APPROVED")

    response = client.post("/api/landing/leads", json=_payload())

    assert response.status_code == 503
    assert "license approval" in response.json()["detail"]
    assert not MockN8nClient.calls


def test_commercial_product_prices_are_configurable_and_checkout_remains_disabled(
    client, monkeypatch
):
    monkeypatch.setenv("ZURI_PRICE_PROFESSIONAL_BRL", "34.90")

    products = client.get("/api/landing/config").json()["products"]

    assert products[0]["price_brl"] == "0.00"
    assert products[0]["grid_size"] == 3
    assert products[0]["keywords"] == 1
    assert products[1]["price_brl"] == "34.90"
    assert products[1]["grid_size"] == 7
    assert products[2]["price_brl"] == "97.00"
    assert products[2]["billing"] == "monthly_assisted"
    assert all(not product["available"] and product["checkout_url"] is None for product in products)


def test_invalid_commercial_price_configuration_is_reported(client, monkeypatch):
    monkeypatch.setenv("ZURI_PRICE_EXPRESS_BRL", "not-a-price")

    response = client.get("/api/landing/config")

    assert response.status_code == 503
    assert response.json()["detail"] == "Commercial product configuration is invalid"


def test_commercial_price_rejects_fractional_cents(client, monkeypatch):
    monkeypatch.setenv("ZURI_PRICE_PROFESSIONAL_BRL", "29.999")

    response = client.get("/api/landing/config")

    assert response.status_code == 503


def test_submit_valid_lead_forwards_only_to_configured_n8n_and_returns_capability(client):
    payload = _payload()
    response = client.post(
        "/api/landing/leads",
        json=payload,
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 202
    receipt = response.json()
    assert receipt["accepted"] is True
    assert receipt["request_id"] == payload["request_id"]
    assert receipt["status_token"] == payload["status_token"]
    assert len(MockN8nClient.calls) == 1
    url, forwarded = MockN8nClient.calls[0]
    assert url == "https://n8n.example.test/webhook/leads"
    assert forwarded["privacy_acknowledged"] is True
    assert forwarded["marketing_consent"] is False
    assert forwarded["google_maps_url"] is None
    assert forwarded["email"] == "ana@example.com"


def test_submission_rejects_cross_origin_and_preexisting_request_receipts(client):
    cross_origin = client.post(
        "/api/landing/leads",
        json=_payload(),
        headers={"Origin": "https://attacker.example"},
    )
    assert cross_origin.status_code == 403
    assert not MockN8nClient.calls

    existing_id = str(uuid.uuid4())
    MockN8nClient.response_factory = lambda _url, _payload: MockN8nResponse(
        {
            "accepted": True,
            "request_id": existing_id,
            "status": "queued",
            "duplicate": True,
        }
    )
    response = client.post("/api/landing/leads", json=_payload())
    assert response.status_code == 409
    assert existing_id not in response.text


@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"privacy_acknowledged": False}, 422),
        ({"campaign_id": "unconfigured-campaign"}, 422),
        ({"google_maps_url": "https://example.com/place"}, 422),
        ({"email": "not-an-email"}, 422),
        ({"request_id": "not-a-uuid"}, 422),
        ({"status_token": "not-a-capability"}, 422),
        ({"contact_name": "  "}, 422),
        ({"whatsapp": "++5511999999999"}, 422),
        ({"privacy_acknowledged": "true"}, 422),
    ],
)
def test_invalid_leads_are_rejected_without_n8n_calls(client, changes, expected_status):
    response = client.post("/api/landing/leads", json=_payload(**changes))
    assert response.status_code == expected_status
    assert not MockN8nClient.calls


def test_lead_service_failures_are_sanitized(client):
    MockN8nClient.response_factory = lambda _url, _payload: MockN8nResponse(
        {"detail": "sensitive"}, status_code=503
    )

    response = client.post("/api/landing/leads", json=_payload())
    assert response.status_code == 503
    assert "sensitive" not in response.text


def test_lead_webhook_timeout_returns_retryable_503(client):
    def timeout_response(_url, _payload):
        raise httpx.ReadTimeout("simulated webhook timeout")

    MockN8nClient.response_factory = timeout_response

    response = client.post("/api/landing/leads", json=_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Integration service timed out; retry the same request"
    )
    assert MockN8nClient.timeouts[0].read == 30


@pytest.mark.parametrize("error_type", [httpx.RemoteProtocolError, httpx.ProxyError])
def test_lead_transport_errors_return_sanitized_retryable_503(client, error_type):
    def failed_response(_url, _payload):
        raise error_type("sensitive provider details")

    MockN8nClient.response_factory = failed_response
    response = client.post("/api/landing/leads", json=_payload())
    assert response.status_code == 503
    assert "retry the same request" in response.json()["detail"]
    assert "sensitive" not in response.text


def test_lead_retry_uses_same_payload_and_honors_retry_after(client, monkeypatch):
    payload = _payload()
    accepted = MockN8nResponse({
        "accepted": True, "request_id": payload["request_id"],
        "status": "queued", "duplicate": True,
    })
    responses = iter([
        MockN8nResponse({"detail": "busy"}, status_code=429, headers={"Retry-After": "2"}),
        accepted,
    ])
    MockN8nClient.response_factory = lambda _url, _payload: next(responses)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(landing.asyncio, "sleep", record_sleep)
    response = client.post("/api/landing/leads", json=payload)

    assert response.status_code == 202
    assert len(MockN8nClient.calls) == 2
    assert MockN8nClient.calls[0] == MockN8nClient.calls[1]
    assert MockN8nClient.calls[0][1]["request_id"] == payload["request_id"]
    assert delays == [2]


def test_lead_retry_after_http_date_is_not_retried_early(client, monkeypatch):
    payload = _payload()
    retry_at = format_datetime(
        datetime.now(timezone.utc) + timedelta(seconds=30),
        usegmt=True,
    )
    responses = iter([
        MockN8nResponse(
            {"detail": "busy"},
            status_code=429,
            headers={"Retry-After": retry_at},
        ),
        MockN8nResponse({
            "accepted": True,
            "request_id": payload["request_id"],
            "status": "queued",
        }),
    ])
    MockN8nClient.response_factory = lambda _url, _payload: next(responses)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(landing.asyncio, "sleep", record_sleep)
    response = client.post("/api/landing/leads", json=payload)

    assert response.status_code == 202
    assert len(MockN8nClient.calls) == 2
    assert 0 < delays[0] <= 30


def test_lead_retry_attempt_limit_is_configurable(client, monkeypatch):
    monkeypatch.setenv("ZURI_N8N_RETRY_ATTEMPTS", "2")
    MockN8nClient.response_factory = lambda _url, _payload: MockN8nResponse(
        {"detail": "unavailable"}, status_code=503
    )
    async def no_wait(_delay):
        return None

    monkeypatch.setattr(landing.asyncio, "sleep", no_wait)
    response = client.post("/api/landing/leads", json=_payload())

    assert response.status_code == 503
    assert len(MockN8nClient.calls) == 2


def test_landing_n8n_timeout_is_configurable_and_bounded(client, monkeypatch):
    monkeypatch.setenv("ZURI_N8N_TIMEOUT_SECONDS", "45")
    response = client.post("/api/landing/leads", json=_payload())

    assert response.status_code == 202
    assert MockN8nClient.timeouts[-1].read == 45

    monkeypatch.setenv("ZURI_N8N_TIMEOUT_SECONDS", "121")
    invalid = client.post("/api/landing/leads", json=_payload())
    assert invalid.status_code == 503
    assert "misconfigured" in invalid.json()["detail"]


def test_status_and_report_capabilities_are_sent_in_post_body_not_url(client):
    request_id = str(uuid.uuid4())
    MockN8nClient.response_factory = lambda _url, _payload: MockN8nResponse(
        {
            "authorized": True,
            "request_id": request_id,
            "status": "processing",
            "progress": 3,
            "total": 9,
            "finished": None,
            "report_ready": False,
        }
    )
    status = client.post(
        f"/api/landing/status/{request_id}",
        json={"status_token": "b" * 64},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "processing"
    assert MockN8nClient.calls[-1] == (
        "https://n8n.example.test/webhook/status",
        {"request_id": request_id, "status_token": "b" * 64},
    )

    MockN8nClient.response_factory = lambda _url, _payload: MockN8nResponse(
        {"authorized": True, "report": {"scan_id": "scan-private"}}
    )
    report = client.post(
        f"/api/landing/report/{request_id}",
        json={"status_token": "b" * 64},
    )
    assert report.status_code == 200
    assert report.json()["report"]["scan_id"] == "scan-private"
    assert MockN8nClient.calls[-1] == (
        "https://n8n.example.test/webhook/report",
        {"request_id": request_id, "status_token": "b" * 64},
    )


def test_status_rejects_malformed_capability_and_unconfigured_webhook(client, monkeypatch):
    request_id = str(uuid.uuid4())
    malformed = client.post(
        f"/api/landing/status/{request_id}",
        json={"status_token": "short"},
    )
    assert malformed.status_code == 422

    monkeypatch.delenv("ZURI_N8N_STATUS_WEBHOOK_URL")
    unavailable = client.post(
        f"/api/landing/status/{request_id}",
        json={"status_token": "c" * 64},
    )
    assert unavailable.status_code == 503


def test_submission_requires_a_configured_privacy_policy(client, monkeypatch):
    monkeypatch.delenv("ZURI_PRIVACY_POLICY_VERSION")
    response = client.post("/api/landing/leads", json=_payload())
    assert response.status_code == 503
    assert not MockN8nClient.calls


def test_campaign_query_must_be_allowlisted(client, monkeypatch):
    monkeypatch.setenv("ZURI_ALLOWED_CAMPAIGNS", "event-test,event-second")
    config = client.get("/api/landing/config?campaign=event-second").json()
    assert config["campaign_id"] == "event-second"
    rejected = client.post(
        "/api/landing/leads",
        json=_payload(campaign_id="spoofed-event"),
    )
    assert rejected.status_code == 422
