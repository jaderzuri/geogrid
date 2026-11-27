import asyncio
import json

import httpx
import pytest

from app.fetchers.base import FetchStatusError
from app.fetchers.dataforseo import DataForSEOFetcher, ENDPOINT


class Resolver:
    async def resolve_business(self, query):
        return {"name": query}

    async def close(self):
        pass


def install_transport(monkeypatch, handler):
    async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)


def response_for(code=20000, message="Ok.", items=None):
    task = {"status_code": code, "status_message": message}
    if items is not None:
        task["result"] = [{"items": items}]
    return {"status_code": 20000, "status_message": "Ok.", "tasks": [task]}


@pytest.mark.parametrize(("configured", "expected"), [(None, "pt"), ("es", "es")])
def test_fetch_uses_configurable_language_and_live_endpoint(monkeypatch, configured, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_for(items=[
            {"type": "maps_search", "title": "Empresa", "cid": "123"},
        ]))

    install_transport(monkeypatch, handler)
    monkeypatch.setenv("DATAFORSEO_LOGIN", "test-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "test-password")
    if configured is None:
        monkeypatch.delenv("DATAFORSEO_LANGUAGE_CODE", raising=False)
    else:
        monkeypatch.setenv("DATAFORSEO_LANGUAGE_CODE", configured)

    async def run():
        fetcher = DataForSEOFetcher(Resolver())
        results = await fetcher.fetch("encanador", -23.5, -46.6)
        await fetcher.close()
        return results

    results = asyncio.run(run())
    request = requests[0]
    payload = json.loads(request.content)

    assert str(request.url) == ENDPOINT
    assert payload[0]["language_code"] == expected
    assert results[0]["name"] == "Empresa"


@pytest.mark.parametrize(
    ("code", "message", "outcome", "category"),
    [
        (40100, "Invalid credentials", "error", "authentication_error"),
        (40102, "No Search Results", "no_results", "no_results"),
        (40101, "Search engine error", "error", "technical_error"),
        (40104, "Verify your account", "error", "authentication_error"),
        (40200, "Payment required", "error", "insufficient_balance"),
        (40210, "Insufficient funds", "error", "insufficient_balance"),
        (40201, "Account access paused", "error", "access_denied"),
        (40207, "IP is not whitelisted", "error", "access_denied"),
        (40202, "Rate limit exceeded", "error", "rate_limit"),
        (40205, "Duplicate task limit per hour exceeded", "error", "duplicate_task_limit"),
        (40206, "Duplicate task limit per day exceeded", "error", "duplicate_task_limit"),
        (40209, "Too many simultaneous queries", "error", "rate_limit"),
        (40405, "Insufficient target page text", "error", "request_error"),
        (40406, "Page not submitted for crawling", "error", "request_error"),
        (40408, "Target URL is invalid", "error", "request_error"),
        (40403, "Results expired", "error", "task_unavailable"),
        (20100, "Task created", "pending", "task_pending"),
        (40602, "Task in queue", "pending", "task_pending"),
        (40106, "Task completed with partial results", "error", "partial_results"),
        (50301, "Third-party API unavailable", "error", "technical_error"),
        (50401, "Live-mode task timeout", "error", "technical_error"),
    ],
)
def test_fetch_classifies_provider_codes_without_losing_original_status(
    monkeypatch,
    code,
    message,
    outcome,
    category,
):
    items = [{"type": "maps_search", "title": "Empresa", "cid": "123"}]
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=response_for(code, message, items if code == 40106 else None),
        ),
    )

    async def run():
        fetcher = DataForSEOFetcher(Resolver(), login="private-login", password="private-password")
        try:
            await fetcher.fetch("encanador", -23.5, -46.6)
        finally:
            await fetcher.close()

    with pytest.raises(FetchStatusError) as caught:
        asyncio.run(run())

    error = caught.value
    assert error.api_status_code == code
    assert error.api_status_message == message
    assert error.outcome == outcome
    assert error.category == category
    assert "private-login" not in str(error)
    assert "private-password" not in str(error)
    if code == 40106:
        assert error.results[0]["name"] == "Empresa"


@pytest.mark.parametrize(
    ("http_code", "category"),
    [
        (401, "authentication_error"),
        (402, "insufficient_balance"),
        (404, "request_error"),
        (429, "rate_limit"),
        (500, "technical_error"),
    ],
)
def test_http_errors_are_classified_without_storing_auth_data(monkeypatch, http_code, category):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            http_code,
            json={"status_message": "Provider rejected request"},
            request=request,
        ),
    )

    async def run():
        fetcher = DataForSEOFetcher(Resolver(), login="private-login", password="private-password")
        try:
            await fetcher.fetch("encanador", -23.5, -46.6)
        finally:
            await fetcher.close()

    with pytest.raises(FetchStatusError) as caught:
        asyncio.run(run())

    assert caught.value.api_status_code == http_code
    assert caught.value.api_status_message == "Provider rejected request"
    assert caught.value.category == category
    assert "private-login" not in str(caught.value)
    assert "private-password" not in str(caught.value)


def test_empty_organic_results_are_not_reported_as_rank_20_plus(monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json=response_for(items=[])),
    )

    async def run():
        fetcher = DataForSEOFetcher(Resolver(), login="test-login", password="test-password")
        try:
            await fetcher.fetch("encanador", -23.5, -46.6)
        finally:
            await fetcher.close()

    with pytest.raises(FetchStatusError) as caught:
        asyncio.run(run())

    assert caught.value.outcome == "no_results"
    assert caught.value.api_status_code == 20000
    assert caught.value.api_status_message == "Ok."


def test_additional_contact_fields_are_captured_when_available(monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json=response_for(items=[
            {
                "type": "maps_search",
                "title": "Restaurante Example",
                "cid": "123456789",
                "place_id": "ChIJN1blonMsZAgR...",
                "latitude": -23.5505,
                "longitude": -46.6333,
                "address": "São Paulo, SP",
                "category": "Restaurante",
                "rating": {"value": 4.5, "votes_count": 150},
                "phone": "+5511999999999",
                "url": "https://example.com.br",
                "position": 1,
            },
            {
                "type": "maps_search",
                "title": "Restaurante Sem Contato",
                "cid": "987654321",
                "latitude": -23.5500,
                "longitude": -46.6330,
                "address": "São Paulo, SP",
                "category": "Restaurante",
                "rating": {"value": 3.8, "votes_count": 42},
            },
        ])),
    )

    async def run():
        fetcher = DataForSEOFetcher(Resolver(), login="test-login", password="test-password")
        try:
            results = await fetcher.fetch("restaurante", -23.5, -46.6)
            return results
        finally:
            await fetcher.close()

    results = asyncio.run(run())

    assert len(results) == 2
    
    first = results[0]
    assert first["name"] == "Restaurante Example"
    assert first["phone"] == "+5511999999999"
    assert first["url"] == "https://example.com.br"
    assert first["position"] == 1
    
    second = results[1]
    assert second["name"] == "Restaurante Sem Contato"
    assert second["phone"] is None
    assert second["url"] is None
    assert second["position"] is None

