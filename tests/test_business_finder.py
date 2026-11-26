import asyncio
import concurrent.futures
import time
import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app import business_finder, db, main


SECRET = "test-signing-secret-with-at-least-32-bytes"


@dataclass
class FakeProvider:
    policy: business_finder.ProviderPolicy
    candidates: list[business_finder.BusinessCandidate]
    search_error: Exception | None = None
    resolve_error: Exception | None = None
    search_calls: int = 0
    resolve_calls: int = 0
    search_queries: list[business_finder.LocationQuery] = field(default_factory=list)

    async def search(self, query):
        self.search_calls += 1
        self.search_queries.append(query)
        if self.search_error:
            raise self.search_error
        return self.candidates

    async def resolve(self, reference_id):
        self.resolve_calls += 1
        if self.resolve_error:
            raise self.resolve_error
        return next(
            (item for item in self.candidates if item.reference_id == reference_id),
            None,
        )


def candidate(
    reference_id="ChIJ-business-1",
    name="Cafe Lume",
    city="São Paulo",
    state="SP",
    lat=-23.55,
    lng=-46.63,
    address="Rua Exemplo, 100, São Paulo - SP",
):
    return business_finder.BusinessCandidate(
        reference_id=reference_id,
        name=name,
        address=address,
        city=city,
        state=state,
        category="restaurant",
        place_id=reference_id if reference_id.startswith("ChIJ") else None,
        lat=lat,
        lng=lng,
        maps_url=f"https://maps.google.com/?query_place_id={reference_id}",
    )


def policy(
    provider_id="test_provider",
    *,
    scanner=False,
    storage_fields=frozenset(),
):
    return business_finder.ProviderPolicy(
        provider_id=provider_id,
        license_name="simulated test license",
        source_url="https://example.test/license",
        permanent_storage_fields=storage_fields,
        scanner_permanent_fields=frozenset({"lat", "lng"}) if scanner else frozenset(),
        attribution="Test provider",
        display_map="Test map",
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "business-finder.db")
    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT", "100")
    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_RATE_LIMIT_PER_MINUTE", "100")
    db.init_db()
    with TestClient(main.app) as client:
        yield client


def search_payload(**changes):
    payload = {
        "request_id": str(uuid.uuid4()),
        "business_name": "Café Lume",
        "city": "São Paulo",
        "state": "SP",
        "address": "Rua Informada, 50",
    }
    return {**payload, **changes}


def install_provider(monkeypatch, provider):
    monkeypatch.setattr(business_finder, "_provider_from_config", lambda: provider)


def test_search_returns_candidates_with_provenance_and_no_cache(api, monkeypatch):
    provider = FakeProvider(
        business_finder._provider_policy("google_places"),
        [candidate()],
    )
    install_provider(monkeypatch, provider)

    response = api.post("/api/business-finder/search", json=search_payload())

    assert response.status_code == 200
    body = response.json()
    item = body["candidates"][0]
    assert body["classification"] == "BUSINESS_LOCATED"
    assert body["confirmation_required"] is True
    assert body["diagnostic_allowed"] is False
    assert item["data_provenance"]["provider_id"] == "google_places"
    assert item["data_provenance"]["fields"]["place_id"]["storage_authorized"] is True
    assert item["data_provenance"]["fields"]["lat"]["storage_authorized"] is False
    assert item["data_provenance"]["fields"]["lat"]["valid_until"]
    assert item["data_provenance"]["user_confirmation_grants_no_storage_rights"] is True
    assert response.headers["cache-control"] == "no-store"
    assert provider.search_calls == 1

    with db.connect() as connection:
        saved = connection.execute(
            "SELECT query_hash, classification FROM business_finder_searches"
        ).fetchone()
        stored_businesses = connection.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
        stored_candidates = connection.execute(
            "SELECT COUNT(*) FROM business_finder_provider_refs"
        ).fetchone()[0]
    assert "Café Lume" not in saved["query_hash"]
    assert saved["classification"] == "BUSINESS_LOCATED"
    assert stored_businesses == stored_candidates == 0


@pytest.mark.parametrize(
    ("candidates", "classification"),
    [
        ([], "BUSINESS_NOT_FOUND"),
        ([candidate(name="Outra Empresa")], "BUSINESS_IDENTITY_MISMATCH"),
        ([candidate(city="Campinas", state="SP")], "BUSINESS_LOCATION_MISMATCH"),
        ([candidate(lat=None)], "BUSINESS_INSUFFICIENT_DATA"),
        (
            [
                candidate(reference_id="ChIJ-business-1"),
                candidate(reference_id="ChIJ-business-2"),
            ],
            "BUSINESS_AMBIGUOUS",
        ),
    ],
)
def test_search_classifies_identity_location_and_ambiguity(
    api, monkeypatch, candidates, classification
):
    provider = FakeProvider(policy(), candidates)
    install_provider(monkeypatch, provider)

    response = api.post("/api/business-finder/search", json=search_payload())

    assert response.status_code == 200
    assert response.json()["classification"] == classification
    if classification == "BUSINESS_NOT_FOUND":
        actions = response.json()["alternative_actions"]
        assert actions["review_submitted_details"] is True
        assert actions["provide_google_maps_link_or_place_id"] is True
        assert actions["request_digital_presence_review"]["available"] is False
        assert "does not prove" in actions["statement"]


def test_search_accepts_place_id_from_valid_maps_link(api, monkeypatch):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        [candidate()],
    )
    install_provider(monkeypatch, provider)
    body = search_payload(
        business_name=None,
        city=None,
        state=None,
        google_maps_url="https://www.google.com/maps/search/?api=1&query_place_id=ChIJ-business-1",
    )

    response = api.post("/api/business-finder/search", json=body)

    assert response.status_code == 200
    assert response.json()["classification"] == "BUSINESS_LOCATED"


def test_invalid_maps_url_does_not_call_provider(api, monkeypatch):
    provider = FakeProvider(policy(), [candidate()])
    install_provider(monkeypatch, provider)

    response = api.post(
        "/api/business-finder/search",
        json=search_payload(google_maps_url="https://attacker.example/?place_id=ChIJ-x"),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "INVALID_INPUT"
    assert provider.search_calls == 0


def test_invalid_place_id_and_state_are_rejected(api, monkeypatch):
    provider = FakeProvider(policy(), [candidate()])
    install_provider(monkeypatch, provider)

    invalid_place_id = api.post(
        "/api/business-finder/search",
        json=search_payload(place_id="bad/place"),
    )
    invalid_state = api.post(
        "/api/business-finder/search",
        json=search_payload(state="ZZ"),
    )

    assert invalid_place_id.status_code == 422
    assert invalid_state.status_code == 422
    assert provider.search_calls == 0


@pytest.mark.parametrize(
    "candidates",
    [[], [candidate(reference_id="ChIJ-another-place")]],
)
def test_unresolved_or_mismatched_place_id_is_not_confirmable(
    api, monkeypatch, candidates
):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        candidates,
    )
    install_provider(monkeypatch, provider)

    response = api.post(
        "/api/business-finder/search",
        json=search_payload(place_id="ChIJ-requested-place"),
    )

    assert response.status_code == 200
    assert response.json()["classification"] == "INVALID_PLACE_ID"
    assert response.json()["candidates"] == []


def test_osm_provider_does_not_ignore_google_place_id(api, monkeypatch):
    provider = FakeProvider(
        policy("osm_nominatim", scanner=True),
        [candidate()],
    )
    install_provider(monkeypatch, provider)

    response = api.post(
        "/api/business-finder/search",
        json=search_payload(place_id="ChIJ-requested-place"),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "INVALID_PLACE_ID"
    assert provider.search_calls == 0


def test_google_places_adapter_classifies_transport_and_http_failures():
    class FakeHttpClient:
        def __init__(self, *, status_code=None, error=None):
            self.status_code = status_code
            self.error = error
            self.request_calls = []

        async def request(self, method, url, **kwargs):
            self.request_calls.append((method, url, kwargs))
            if self.error:
                raise self.error
            return type("Response", (), {"status_code": self.status_code})()

    async def verify(client):
        return await business_finder.GooglePlacesProvider._request(
            client,
            "GET",
            "https://places.googleapis.com/v1/places/test",
        )

    for status_code, category in ((401, "AUTHENTICATION_ERROR"), (403, "AUTHENTICATION_ERROR"),
                                  (429, "RATE_LIMIT"), (404, "BUSINESS_NOT_FOUND"),
                                  (500, "TEMPORARY_ERROR"), (400, "API_CONTRACT_ERROR")):
        with pytest.raises(business_finder.ProviderError) as error:
            asyncio.run(verify(FakeHttpClient(status_code=status_code)))
        assert error.value.category == category

    client = FakeHttpClient(status_code=200)
    asyncio.run(verify(client))
    assert len(client.request_calls) == 1

    with pytest.raises(business_finder.ProviderError) as timeout:
        asyncio.run(verify(FakeHttpClient(error=httpx.TimeoutException("timeout"))))
    assert timeout.value.category == "TEMPORARY_ERROR"
    assert timeout.value.uncertain is True


def test_daily_budget_and_rate_limit_fail_closed(api, monkeypatch):
    provider = FakeProvider(policy(), [candidate()])
    install_provider(monkeypatch, provider)
    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT", "0")

    budget = api.post("/api/business-finder/search", json=search_payload())
    assert budget.status_code == 429
    assert budget.json()["detail"]["category"] == "BUDGET_LIMIT"
    assert provider.search_calls == 0

    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT", "10")
    monkeypatch.setenv("GEOGRID_BUSINESS_FINDER_RATE_LIMIT_PER_MINUTE", "0")
    rate = api.post("/api/business-finder/search", json=search_payload())
    assert rate.status_code == 429
    assert rate.json()["detail"]["category"] == "RATE_LIMIT"
    assert provider.search_calls == 0


def test_same_search_request_never_repeats_provider_call(api, monkeypatch):
    provider = FakeProvider(policy(), [candidate()])
    install_provider(monkeypatch, provider)
    body = search_payload()

    first = api.post("/api/business-finder/search", json=body)
    duplicate = api.post("/api/business-finder/search", json=body)

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["category"] == "DUPLICATE_REQUEST"
    assert provider.search_calls == 1


def test_uncertain_provider_failure_is_not_retried(api, monkeypatch):
    provider = FakeProvider(
        policy(),
        [],
        search_error=business_finder.ProviderError("TEMPORARY_ERROR", uncertain=True),
    )
    install_provider(monkeypatch, provider)
    body = search_payload()

    first = api.post("/api/business-finder/search", json=body)
    second = api.post("/api/business-finder/search", json=body)

    assert first.status_code == 503
    assert first.json()["detail"]["category"] == "TEMPORARY_ERROR"
    assert first.json()["detail"]["retry_same_request_id"] is False
    assert second.status_code == 409
    assert second.json()["detail"]["category"] == "SUBMISSION_UNCERTAIN"
    assert provider.search_calls == 1


def test_authentication_and_invalid_provider_response_are_classified(api, monkeypatch):
    def missing_credentials():
        raise business_finder.ProviderError("AUTHENTICATION_ERROR")

    monkeypatch.setattr(business_finder, "_provider_from_config", missing_credentials)
    auth = api.post("/api/business-finder/search", json=search_payload())
    assert auth.status_code == 502
    assert auth.json()["detail"]["category"] == "AUTHENTICATION_ERROR"

    invalid_provider = FakeProvider(
        policy(),
        [],
        search_error=business_finder.ProviderError("API_CONTRACT_ERROR"),
    )
    install_provider(monkeypatch, invalid_provider)
    invalid = api.post("/api/business-finder/search", json=search_payload())
    assert invalid.status_code == 502
    assert invalid.json()["detail"]["category"] == "API_CONTRACT_ERROR"


def test_confirmation_is_explicit_single_use_and_token_contains_no_provider_fields(
    api, monkeypatch
):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        [candidate()],
    )
    install_provider(monkeypatch, provider)
    search = api.post("/api/business-finder/search", json=search_payload()).json()
    token = search["candidates"][0]["candidate_token"]

    rejected = api.post(
        "/api/business-finder/confirm",
        json={"request_id": str(uuid.uuid4()), "candidate_token": token, "confirmed": False},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["category"] == "CONFIRMATION_REQUIRED"

    confirmation_id = str(uuid.uuid4())
    accepted = api.post(
        "/api/business-finder/confirm",
        json={"request_id": confirmation_id, "candidate_token": token, "confirmed": True},
    )
    repeated = api.post(
        "/api/business-finder/confirm",
        json={"request_id": confirmation_id, "candidate_token": token, "confirmed": True},
    )
    assert accepted.status_code == repeated.status_code == 200
    assert accepted.json()["confirmation_token"] == repeated.json()["confirmation_token"]
    claims = business_finder._decode_token(accepted.json()["confirmation_token"], SECRET.encode())
    assert claims["stage"] == "confirmed"
    assert "name" not in claims
    assert "lat" not in claims
    assert accepted.json()["diagnostic_allowed"] is False


def test_confirmation_request_id_cannot_be_reused_for_another_candidate(api, monkeypatch):
    provider = FakeProvider(
        policy(),
        [
            candidate(reference_id="candidate-one"),
            candidate(reference_id="candidate-two"),
        ],
    )
    install_provider(monkeypatch, provider)
    candidates = api.post(
        "/api/business-finder/search", json=search_payload()
    ).json()["candidates"]
    confirmation_id = str(uuid.uuid4())

    first = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": confirmation_id,
            "candidate_token": candidates[0]["candidate_token"],
            "confirmed": True,
        },
    )
    conflict = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": confirmation_id,
            "candidate_token": candidates[1]["candidate_token"],
            "confirmed": True,
        },
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["category"] == "REQUEST_ID_CONFLICT"


def test_osm_storage_requires_attribution_readiness(monkeypatch):
    monkeypatch.setenv("GEOGRID_OSM_ODBL_STORAGE_ACCEPTED", "true")
    monkeypatch.setenv("GEOGRID_NOMINATIM_BASE_URL", "https://nominatim.example.test")
    monkeypatch.delenv("GEOGRID_OSM_ATTRIBUTION_READY", raising=False)

    blocked = business_finder._provider_policy("osm_nominatim")

    monkeypatch.setenv("GEOGRID_OSM_ATTRIBUTION_READY", "true")
    enabled = business_finder._provider_policy("osm_nominatim")

    assert blocked.permanent_storage_fields == frozenset()
    assert blocked.scanner_permanent_fields == frozenset()
    assert {"lat", "lng"}.issubset(enabled.scanner_permanent_fields)


def test_diagnostic_requires_confirmation_and_blocks_google_coordinates(api, monkeypatch):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        [candidate()],
    )
    install_provider(monkeypatch, provider)
    search_body = search_payload()
    search_result = api.post("/api/business-finder/search", json=search_body).json()
    token = search_result["candidates"][0]["candidate_token"]
    confirmed = api.post(
        "/api/business-finder/confirm",
        json={"request_id": str(uuid.uuid4()), "candidate_token": token, "confirmed": True},
    ).json()["confirmation_token"]
    diagnostic_payload = {
        "request_id": str(uuid.uuid4()),
        "confirmation_token": confirmed,
        "confirmed": False,
        "search": search_body,
        "keywords": ["restaurante"],
    }

    no_confirmation = api.post("/api/business-finder/diagnostics", json=diagnostic_payload)
    diagnostic_payload["confirmed"] = True
    denied = api.post("/api/business-finder/diagnostics", json=diagnostic_payload)

    assert no_confirmation.status_code == 422
    assert no_confirmation.json()["detail"]["category"] == "CONFIRMATION_REQUIRED"
    assert denied.status_code == 409
    assert denied.json()["detail"]["category"] == "SOURCE_NOT_AUTHORIZED"
    assert denied.json()["detail"]["diagnostic_started"] is False
    assert provider.resolve_calls == 0
    assert db.list_scans() == []


def test_authorized_provider_creates_idempotent_scan_with_field_provenance(
    api, monkeypatch
):
    provider = FakeProvider(
        policy(
            "osm_self_hosted",
            scanner=True,
            storage_fields=frozenset(
                {"provider_reference_id", "name", "address", "lat", "lng"}
            ),
        ),
        [
            business_finder.BusinessCandidate(
                reference_id="N12345",
                name="Cafe Lume",
                address="Rua externa, 900",
                city="São Paulo",
                state="São Paulo",
                category="restaurant",
                place_id=None,
                lat=-23.55,
                lng=-46.63,
                maps_url="https://www.openstreetmap.org/node/12345",
            )
        ],
    )
    install_provider(monkeypatch, provider)

    async def no_op(scan_ids):
        assert scan_ids

    monkeypatch.setattr(main, "_run_queued", no_op)
    search_body = search_payload()
    candidate_token = api.post(
        "/api/business-finder/search", json=search_body
    ).json()["candidates"][0]["candidate_token"]
    confirmation_token = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": str(uuid.uuid4()),
            "candidate_token": candidate_token,
            "confirmed": True,
        },
    ).json()["confirmation_token"]
    diagnostic_request_id = str(uuid.uuid4())
    body = {
        "request_id": diagnostic_request_id,
        "confirmation_token": confirmation_token,
        "confirmed": True,
        "search": search_body,
        "keywords": ["restaurante"],
    }

    created = api.post("/api/business-finder/diagnostics", json=body)
    repeated = api.post("/api/business-finder/diagnostics", json=body)

    assert created.status_code == repeated.status_code == 200
    assert created.json()["created_new"] is True
    assert repeated.json()["created_new"] is False
    assert created.json()["scan_ids"] == repeated.json()["scan_ids"]
    assert provider.resolve_calls == 1
    scan = db.get_scan(created.json()["scan_ids"][0])
    assert scan["business_name"] == "Café Lume"
    with db.connect() as connection:
        saved_business = connection.execute(
            "SELECT address FROM businesses WHERE id = ?",
            (scan["business_id"],),
        ).fetchone()
    assert saved_business["address"] == "Rua Informada, 50, São Paulo, SP"
    assert len(scan["points"]) == 9
    assert all(point["status"] == "pending" for point in scan["points"])
    with db.connect() as connection:
        provenance = connection.execute(
            "SELECT field_name, data_origin, provider_id, storage_authorized "
            "FROM business_finder_data_provenance ORDER BY field_name"
        ).fetchall()
        provider_ref_count = connection.execute(
            "SELECT COUNT(*) FROM business_finder_provider_refs"
        ).fetchone()[0]
    assert {row["data_origin"] for row in provenance} == {"user_submitted", "provider"}
    assert all(row["storage_authorized"] == 1 for row in provenance)
    assert provider_ref_count == 1


def test_place_id_only_google_candidate_cannot_start_persistent_diagnostic(
    api, monkeypatch
):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        [candidate()],
    )
    install_provider(monkeypatch, provider)

    search_body = search_payload(
        business_name=None,
        city=None,
        state=None,
        address=None,
        place_id="ChIJ-business-1",
    )
    search = api.post("/api/business-finder/search", json=search_body).json()
    confirmation = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": str(uuid.uuid4()),
            "candidate_token": search["candidates"][0]["candidate_token"],
            "confirmed": True,
        },
    ).json()["confirmation_token"]

    response = api.post(
        "/api/business-finder/diagnostics",
        json={
            "request_id": str(uuid.uuid4()),
            "confirmation_token": confirmation,
            "confirmed": True,
            "search": search_body,
            "keywords": ["cafeteria"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["category"] == "SOURCE_NOT_AUTHORIZED"
    assert response.json()["detail"]["diagnostic_started"] is False
    assert provider.resolve_calls == 0
    assert db.list_scans() == []


def test_search_reservation_enforces_daily_budget_under_concurrency(api):
    assert api.app is main.app
    usage_date = "2030-01-01"

    def reserve(index):
        try:
            db.begin_business_finder_search(
                request_id=f"parallel-{index}",
                query_hash=f"query-{index}",
                provider_id="mock",
                subject_hash=f"subject-{index}",
                usage_date=usage_date,
                bucket_start=100,
                rate_limit=100,
                daily_limit=5,
            )
            return True
        except db.BusinessFinderBudgetLimitError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(reserve, range(20)))

    assert sum(outcomes) == 5
    with db.connect() as connection:
        assert connection.execute(
            "SELECT query_count FROM business_finder_daily_usage WHERE usage_date = ?",
            (usage_date,),
        ).fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM business_finder_searches WHERE status = 'started'"
        ).fetchone()[0] == 5


def test_expired_confirmation_cannot_start_diagnostic(api, monkeypatch):
    provider = FakeProvider(
        policy("google_places", storage_fields=frozenset({"place_id"})),
        [candidate()],
    )
    install_provider(monkeypatch, provider)
    search_body = search_payload()
    search = api.post("/api/business-finder/search", json=search_body).json()
    claims = business_finder._decode_token(
        search["candidates"][0]["candidate_token"], SECRET.encode()
    )
    expired_nonce = uuid.uuid4().hex
    db.create_business_finder_confirmation(
        business_finder._nonce_hash(expired_nonce),
        1,
    )
    signed_but_expired = business_finder._candidate_token(
        provider_id=claims["provider_id"],
        reference_id=claims["reference_id"],
        request_id=claims["search_request_id"],
        query_hash=claims["query_hash"],
        nonce=expired_nonce,
        stage="candidate",
        expires_at=int(time.time()) + 600,
        secret=SECRET.encode(),
    )

    response = api.post(
        "/api/business-finder/confirm",
        json={
            "request_id": str(uuid.uuid4()),
            "candidate_token": signed_but_expired,
            "confirmed": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["category"] == "CONFIRMATION_REQUIRED"
