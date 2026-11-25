"""Business discovery with explicit data provenance and provider-policy gates."""

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import parse_qs, quote, urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from . import db

router = APIRouter(prefix="/api/business-finder", tags=["business-finder"])
log = logging.getLogger("geogrid.business_finder")

_TOKEN_TTL_SECONDS = 600
_GOOGLE_PLACE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_OSM_REFERENCE_PATTERN = re.compile(r"[NWR][0-9]{1,20}\Z")
_GOOGLE_MAPS_HOSTS = {
    "google.com",
    "www.google.com",
    "maps.google.com",
    "maps.app.goo.gl",
    "goo.gl",
}
_STATE_CODES = {
    "acre": "AC",
    "alagoas": "AL",
    "amapa": "AP",
    "amazonas": "AM",
    "bahia": "BA",
    "ceara": "CE",
    "distrito federal": "DF",
    "espirito santo": "ES",
    "goias": "GO",
    "maranhao": "MA",
    "mato grosso": "MT",
    "mato grosso do sul": "MS",
    "minas gerais": "MG",
    "para": "PA",
    "paraiba": "PB",
    "parana": "PR",
    "pernambuco": "PE",
    "piaui": "PI",
    "rio de janeiro": "RJ",
    "rio grande do norte": "RN",
    "rio grande do sul": "RS",
    "rondonia": "RO",
    "roraima": "RR",
    "santa catarina": "SC",
    "sao paulo": "SP",
    "sergipe": "SE",
    "tocantins": "TO",
}
_BRAZIL_UFS = frozenset(_STATE_CODES.values())


if hasattr(BaseModel, "model_fields"):
    from pydantic import ConfigDict

    class FinderInput(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
else:

    class FinderInput(BaseModel):
        class Config:
            extra = "forbid"
            anystr_strip_whitespace = True


class BusinessSearchIn(FinderInput):
    request_id: UUID
    business_name: str | None = Field(default=None, min_length=2, max_length=200)
    city: str | None = Field(default=None, min_length=2, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=80)
    address: str | None = Field(default=None, min_length=2, max_length=300)
    google_maps_url: str | None = Field(default=None, max_length=2000)
    place_id: str | None = Field(default=None, min_length=1, max_length=256)


class BusinessConfirmIn(FinderInput):
    request_id: UUID
    candidate_token: str = Field(min_length=30, max_length=4096)
    confirmed: StrictBool


class BusinessDiagnosticIn(FinderInput):
    request_id: UUID
    confirmation_token: str = Field(min_length=30, max_length=4096)
    confirmed: StrictBool
    search: BusinessSearchIn
    keywords: list[str] = Field(min_length=1, max_length=10)
    grid_size: int = Field(default=3, ge=3, le=13)
    spacing_km: float = Field(default=1.0, gt=0, le=50)


@dataclass(frozen=True)
class ProviderPolicy:
    provider_id: str
    license_name: str
    source_url: str
    permanent_storage_fields: frozenset[str]
    scanner_permanent_fields: frozenset[str]
    attribution: str | None
    display_map: str | None
    valid_for_days: int | None = None

    def public_data(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "license_name": self.license_name,
            "source_url": self.source_url,
            "permanent_storage_fields": sorted(self.permanent_storage_fields),
            "scanner_permanent_fields": sorted(self.scanner_permanent_fields),
            "attribution": self.attribution,
            "display_map": self.display_map,
            "valid_for_days": self.valid_for_days,
        }

@dataclass(frozen=True)
class LocationQuery:
    business_name: str | None
    city: str | None
    state: str | None
    address: str | None
    place_id: str | None
    google_maps_url: str | None


@dataclass(frozen=True)
class BusinessCandidate:
    reference_id: str
    name: str | None
    address: str | None
    city: str | None
    state: str | None
    category: str | None
    place_id: str | None
    lat: float | None
    lng: float | None
    maps_url: str | None


class BusinessLocationProvider(Protocol):
    policy: ProviderPolicy

    async def search(self, query: LocationQuery) -> list[BusinessCandidate]: ...

    async def resolve(self, reference_id: str) -> BusinessCandidate | None: ...


class ProviderError(Exception):
    def __init__(self, category: str, *, uncertain: bool = False):
        self.category = category
        self.uncertain = uncertain


class BusinessFinderDuplicateRequestError(Exception):
    def __init__(self, status: str):
        self.status = status


class BusinessFinderProviderDisabledError(Exception):
    pass


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _signing_secret() -> bytes:
    secret = os.environ.get("GEOGRID_BUSINESS_FINDER_SIGNING_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail={
                "category": "AUTHENTICATION_ERROR",
                "message": "Business finder signing is not configured",
            },
        )
    return secret.encode("utf-8")


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold().strip()


def _state_key(value: str | None) -> str:
    normalized = _normalize(value)
    if len(normalized) == 2:
        return normalized.upper()
    return _STATE_CODES.get(normalized, normalized).upper()


def _extract_place_id(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in _GOOGLE_MAPS_HOSTS
        or parsed.username
        or parsed.password
    ):
        return None
    query = parse_qs(parsed.query)
    for key in ("query_place_id", "place_id"):
        candidate = query.get(key, [None])[0]
        if candidate and _GOOGLE_PLACE_ID_PATTERN.fullmatch(candidate):
            return candidate
    match = re.search(r"!1s(ChIJ[A-Za-z0-9_-]+)", parsed.path)
    return match.group(1) if match else None


def _query_from_input(body: BusinessSearchIn) -> LocationQuery:
    place_id = body.place_id.strip() if body.place_id else None
    maps_url = body.google_maps_url.strip() if body.google_maps_url else None
    if maps_url:
        try:
            parsed = urlparse(maps_url)
            host = (parsed.hostname or "").lower()
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"category": "INVALID_INPUT", "message": "Invalid Google Maps URL"},
            ) from error
        if (
            parsed.scheme != "https"
            or host not in _GOOGLE_MAPS_HOSTS
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(
                status_code=422,
                detail={"category": "INVALID_INPUT", "message": "Invalid Google Maps URL"},
            )
        place_id = place_id or _extract_place_id(maps_url)
    if place_id and not _GOOGLE_PLACE_ID_PATTERN.fullmatch(place_id):
        raise HTTPException(
            status_code=422,
            detail={"category": "INVALID_INPUT", "message": "Invalid Place ID"},
        )

    name = body.business_name.strip() if body.business_name else None
    city = body.city.strip() if body.city else None
    state = body.state.strip() if body.state else None
    address = body.address.strip() if body.address else None
    if not place_id and not (name and city and state):
        raise HTTPException(
            status_code=422,
            detail={
                "category": "INSUFFICIENT_DATA",
                "message": "Provide a Place ID or business name, city, and state",
            },
        )
    if state and (
        (len(state) == 2 and state.upper() not in _BRAZIL_UFS)
        or (
            len(state) > 2
            and not state.isalpha()
            and not all(char.isalpha() or char.isspace() or char == "-" for char in state)
        )
    ):
        raise HTTPException(
            status_code=422,
            detail={"category": "INVALID_INPUT", "message": "State must be a valid state name or UF"},
        )
    return LocationQuery(name, city, state, address, place_id, maps_url)


def _query_hash(query: LocationQuery, secret: bytes) -> str:
    canonical = json.dumps(
        {
            "business_name": query.business_name,
            "city": query.city,
            "state": query.state,
            "address": query.address,
            "place_id": query.place_id,
            "google_maps_url": query.google_maps_url,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def _token(claims: dict, secret: bytes) -> str:
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def _decode_token(token: str, secret: bytes) -> dict:
    try:
        encoded_text, signature_text = token.split(".", 1)
        encoded = encoded_text.encode("ascii")
        signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        expected = hmac.new(secret, encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        claims = json.loads(payload)
        if (
            claims.get("purpose") != "business_finder"
            or int(claims.get("expires_at", 0)) < int(time.time())
        ):
            raise ValueError
        return claims
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError) as error:
        raise HTTPException(
            status_code=422,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Invalid or expired confirmation token"},
        ) from error


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _provider_policy(provider_id: str) -> ProviderPolicy:
    if provider_id == "google_places":
        return ProviderPolicy(
            provider_id=provider_id,
            license_name="Google Maps Platform Terms and Places API policies",
            source_url="https://developers.google.com/maps/documentation/places/web-service/policies",
            permanent_storage_fields=frozenset({"place_id", "provider_reference_id"}),
            scanner_permanent_fields=frozenset(),
            attribution="Google Maps",
            display_map="Google Maps only for Places API results on a map",
            valid_for_days=30,
        )
    if provider_id == "osm_nominatim":
        accepted = _enabled(os.environ.get("GEOGRID_OSM_ODBL_STORAGE_ACCEPTED"))
        attribution_ready = _enabled(os.environ.get("GEOGRID_OSM_ATTRIBUTION_READY"))
        base_url = os.environ.get("GEOGRID_NOMINATIM_BASE_URL", "").strip()
        try:
            host = (urlparse(base_url).hostname or "").lower() if base_url else ""
        except ValueError:
            host = ""
        custom_instance = bool(host and host != "nominatim.openstreetmap.org")
        storage_fields = (
            frozenset(
                {
                    "provider_reference_id",
                    "lat",
                    "lng",
                    "name",
                    "address",
                    "city",
                    "state",
                }
            )
            if accepted and attribution_ready
            else frozenset()
        )
        scanner_fields = (
            frozenset({"lat", "lng"})
            if accepted and attribution_ready and custom_instance
            else frozenset()
        )
        return ProviderPolicy(
            provider_id=provider_id,
            license_name="Open Database License (ODbL) 1.0",
            source_url="https://www.openstreetmap.org/copyright/",
            permanent_storage_fields=storage_fields,
            scanner_permanent_fields=scanner_fields,
            attribution="© OpenStreetMap contributors — ODbL",
            display_map="MapTiler/OSM map with visible attribution; verify plan terms",
        )
    raise BusinessFinderProviderDisabledError


def _provider_from_config() -> BusinessLocationProvider:
    provider_id = os.environ.get("GEOGRID_BUSINESS_FINDER_PROVIDER", "").strip().lower()
    if provider_id == "google_places":
        api_key = os.environ.get("GEOGRID_GOOGLE_PLACES_API_KEY", "").strip()
        if not api_key:
            raise ProviderError("AUTHENTICATION_ERROR")
        return GooglePlacesProvider(api_key)
    if provider_id == "osm_nominatim":
        base_url = os.environ.get("GEOGRID_NOMINATIM_BASE_URL", "").strip()
        user_agent = os.environ.get("GEOGRID_NOMINATIM_USER_AGENT", "").strip()
        if not base_url or not user_agent:
            raise ProviderError("AUTHENTICATION_ERROR")
        try:
            parsed = urlparse(base_url)
            host = parsed.hostname
        except ValueError as error:
            raise ProviderError("API_CONTRACT_ERROR") from error
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ProviderError("API_CONTRACT_ERROR")
        if host.lower() == "nominatim.openstreetmap.org":
            raise ProviderError("PROVIDER_DISABLED")
        return NominatimProvider(base_url.rstrip("/"), user_agent)
    raise BusinessFinderProviderDisabledError


class GooglePlacesProvider:
    policy = _provider_policy("google_places")

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: LocationQuery) -> list[BusinessCandidate]:
        if query.place_id:
            candidate = await self.resolve(query.place_id)
            return [candidate] if candidate else []
        text_query = ", ".join(
            part for part in (
                query.business_name,
                query.address,
                query.city,
                query.state,
            ) if part
        )
        async with httpx.AsyncClient(timeout=10) as client:
            response = await self._request(
                client,
                "POST",
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": (
                        "places.id,places.displayName,places.formattedAddress,"
                        "places.addressComponents,places.location,"
                        "places.primaryTypeDisplayName,places.googleMapsUri"
                    ),
                },
                json={"textQuery": text_query, "languageCode": "pt-BR", "maxResultCount": 5},
            )
        payload = self._json(response)
        places = payload.get("places", [])
        if not isinstance(places, list):
            raise ProviderError("API_CONTRACT_ERROR")
        return [candidate for place in places[:5] if (candidate := self._candidate(place))]

    async def resolve(self, reference_id: str) -> BusinessCandidate | None:
        if not _GOOGLE_PLACE_ID_PATTERN.fullmatch(reference_id):
            raise ProviderError("API_CONTRACT_ERROR")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await self._request(
                    client,
                    "GET",
                    f"https://places.googleapis.com/v1/places/{quote(reference_id, safe='')}",
                    headers={
                        "X-Goog-Api-Key": self.api_key,
                        "X-Goog-FieldMask": (
                            "id,displayName,formattedAddress,addressComponents,location,"
                            "primaryTypeDisplayName,googleMapsUri"
                        ),
                    },
                )
            payload = self._json(response)
        except ProviderError as error:
            if error.category == "BUSINESS_NOT_FOUND":
                return None
            raise
        return self._candidate(payload)

    @staticmethod
    async def _request(client, method: str, url: str, **kwargs):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as error:
            raise ProviderError("TEMPORARY_ERROR", uncertain=True) from error
        except httpx.RequestError as error:
            raise ProviderError("TEMPORARY_ERROR", uncertain=True) from error
        if response.status_code in {401, 403}:
            raise ProviderError("AUTHENTICATION_ERROR")
        if response.status_code == 429:
            raise ProviderError("RATE_LIMIT")
        if response.status_code == 404:
            raise ProviderError("BUSINESS_NOT_FOUND")
        if response.status_code >= 500:
            raise ProviderError("TEMPORARY_ERROR", uncertain=True)
        if response.status_code >= 400:
            raise ProviderError("API_CONTRACT_ERROR")
        return response

    @staticmethod
    def _json(response) -> dict:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise ProviderError("API_CONTRACT_ERROR") from error
        if not isinstance(payload, dict):
            raise ProviderError("API_CONTRACT_ERROR")
        return payload

    @staticmethod
    def _candidate(place: dict) -> BusinessCandidate | None:
        if not isinstance(place, dict):
            return None
        location = place.get("location") or {}
        if not isinstance(location, dict):
            location = {}
        components = place.get("addressComponents") or []
        if not isinstance(components, list):
            components = []
        city = None
        state = None
        for component in components:
            if not isinstance(component, dict):
                continue
            types = component.get("types") or []
            if "locality" in types or "postal_town" in types:
                city = component.get("longText") or component.get("shortText")
            if "administrative_area_level_1" in types:
                state = component.get("shortText") or component.get("longText")
        display_name = place.get("displayName")
        name = display_name.get("text") if isinstance(display_name, dict) else None
        place_id = place.get("id")
        if place_id and not _GOOGLE_PLACE_ID_PATTERN.fullmatch(str(place_id)):
            place_id = None
        try:
            lat = float(location["latitude"])
            lng = float(location["longitude"])
        except (KeyError, TypeError, ValueError):
            lat = lng = None
        primary_type = place.get("primaryTypeDisplayName")
        category = primary_type.get("text") if isinstance(primary_type, dict) else None
        return BusinessCandidate(
            reference_id=str(place_id or ""),
            name=name if isinstance(name, str) else None,
            address=place.get("formattedAddress"),
            city=city,
            state=state,
            category=category,
            place_id=place_id,
            lat=lat,
            lng=lng,
            maps_url=place.get("googleMapsUri"),
        )


class NominatimProvider:
    policy = _provider_policy("osm_nominatim")

    def __init__(self, base_url: str, user_agent: str):
        self.base_url = base_url
        self.user_agent = user_agent[:200]

    async def search(self, query: LocationQuery) -> list[BusinessCandidate]:
        text_query = ", ".join(
            part for part in (
                query.business_name,
                query.address,
                query.city,
                query.state,
            ) if part
        )
        return await self._get(
            "/search",
            {
                "q": text_query,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 5,
            },
        )

    async def resolve(self, reference_id: str) -> BusinessCandidate | None:
        if not _OSM_REFERENCE_PATTERN.fullmatch(reference_id):
            raise ProviderError("API_CONTRACT_ERROR")
        try:
            result = await self._get(
                "/lookup",
                {
                    "osm_ids": reference_id,
                    "format": "jsonv2",
                    "addressdetails": 1,
                },
            )
        except ProviderError:
            raise
        return result[0] if result else None

    async def _get(self, path: str, params: dict) -> list[BusinessCandidate]:
        try:
            async with httpx.AsyncClient(
                timeout=10,
                headers={"User-Agent": self.user_agent},
            ) as client:
                response = await client.get(f"{self.base_url}{path}", params=params)
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise ProviderError("TEMPORARY_ERROR", uncertain=True) from error
        if response.status_code == 429:
            raise ProviderError("RATE_LIMIT")
        if response.status_code >= 500:
            raise ProviderError("TEMPORARY_ERROR", uncertain=True)
        if response.status_code >= 400:
            raise ProviderError("API_CONTRACT_ERROR")
        try:
            rows = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise ProviderError("API_CONTRACT_ERROR") from error
        if not isinstance(rows, list):
            raise ProviderError("API_CONTRACT_ERROR")
        candidates = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            osm_type = str(row.get("osm_type", "")).upper()[:1]
            osm_id = str(row.get("osm_id", ""))
            reference_id = f"{osm_type}{osm_id}"
            if not _OSM_REFERENCE_PATTERN.fullmatch(reference_id):
                continue
            address = row.get("address") or {}
            city = next(
                (
                    address.get(key)
                    for key in ("city", "town", "village", "municipality", "hamlet")
                    if address.get(key)
                ),
                None,
            )
            state = address.get("state")
            try:
                lat = float(row["lat"])
                lng = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                lat = lng = None
            candidates.append(
                BusinessCandidate(
                    reference_id=reference_id,
                    name=row.get("name"),
                    address=row.get("display_name"),
                    city=city,
                    state=state,
                    category=row.get("type") or row.get("class"),
                    place_id=None,
                    lat=lat,
                    lng=lng,
                    maps_url=f"https://www.openstreetmap.org/{reference_id[0].lower()}/{reference_id[1:]}",
                )
            )
        return candidates


def _policy_for(provider: BusinessLocationProvider) -> ProviderPolicy:
    if provider.policy.provider_id == "osm_nominatim":
        return _provider_policy("osm_nominatim")
    return provider.policy


def _subject_hash(request: Request, secret: bytes, provider_id: str) -> str:
    address = request.client.host if request.client else "unknown"
    return hmac.new(
        secret,
        f"{provider_id}:{address}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _candidate_payload(
    candidate: BusinessCandidate,
    policy: ProviderPolicy,
    token: str,
) -> dict:
    now_utc = datetime.now(timezone.utc)
    coordinate_expiry = (
        (now_utc + timedelta(days=policy.valid_for_days)).isoformat(timespec="seconds")
        if policy.valid_for_days is not None
        else None
    )
    fields: dict[str, dict[str, str | bool | None]] = {}
    for field_name in (
        "provider_reference_id",
        "place_id",
        "name",
        "address",
        "city",
        "state",
        "category",
        "google_maps_url",
        "lat",
        "lng",
    ):
        permitted = field_name in policy.permanent_storage_fields
        if field_name == "provider_reference_id" and "place_id" in policy.permanent_storage_fields:
            permitted = True
        field_policy = {
            "storage_authorized": permitted,
            "retention": "permanent" if permitted else "transient_only",
        }
        if field_name in {"lat", "lng"} and policy.valid_for_days is not None:
            field_policy["retention"] = "temporary"
            field_policy["valid_until"] = coordinate_expiry
        fields[field_name] = field_policy
    return {
        "candidate_token": token,
        "provider_reference_id": candidate.reference_id,
        "name": candidate.name,
        "address": candidate.address,
        "city": candidate.city,
        "state": candidate.state,
        "category": candidate.category,
        "place_id": candidate.place_id,
        "coordinates": (
            {"lat": candidate.lat, "lng": candidate.lng}
            if candidate.lat is not None and candidate.lng is not None
            else None
        ),
        "google_maps_url": candidate.maps_url,
        "data_provenance": {
            **policy.public_data(),
            "retrieved_at": now_utc.isoformat(timespec="seconds"),
            "fields": fields,
            "scanner_compatible": {
                "lat",
                "lng",
            }.issubset(policy.scanner_permanent_fields),
            "user_confirmation_grants_no_storage_rights": True,
        },
    }


def _candidate_token(
    *,
    provider_id: str,
    reference_id: str,
    request_id: str,
    query_hash: str,
    nonce: str,
    stage: str,
    expires_at: int,
    secret: bytes,
) -> str:
    return _token(
        {
            "purpose": "business_finder",
            "provider_id": provider_id,
            "reference_id": reference_id,
            "search_request_id": request_id,
            "query_hash": query_hash,
            "nonce": nonce,
            "stage": stage,
            "expires_at": expires_at,
        },
        secret,
    )


def _reserve_provider_call(
    request_id: str,
    query_hash: str,
    provider_id: str,
    request: Request,
    secret: bytes,
    *,
    global_rate: tuple[int, int, int] | None = None,
) -> None:
    now_epoch = int(time.time())
    daily_limit = int(os.environ.get("GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT", "0"))
    rate_limit = int(os.environ.get("GEOGRID_BUSINESS_FINDER_RATE_LIMIT_PER_MINUTE", "10"))
    if daily_limit < 0 or rate_limit < 0:
        raise HTTPException(
            status_code=503,
            detail={"category": "BUDGET_LIMIT", "message": "Business finder limits are invalid"},
        )
    global_hash = None
    global_bucket = None
    global_limit = None
    if global_rate:
        global_hash = hmac.new(
            secret,
            f"provider:{provider_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        window, global_limit, _ = global_rate
        global_bucket = now_epoch // window * window
    try:
        db.begin_business_finder_search(
            request_id=request_id,
            query_hash=query_hash,
            provider_id=provider_id,
            subject_hash=_subject_hash(request, secret, provider_id),
            usage_date=datetime.now(timezone.utc).date().isoformat(),
            bucket_start=now_epoch // 60 * 60,
            rate_limit=rate_limit,
            daily_limit=daily_limit,
            global_subject_hash=global_hash,
            global_bucket_start=global_bucket,
            global_rate_limit=global_limit,
        )
    except db.BusinessFinderBudgetLimitError as error:
        raise HTTPException(
            status_code=429,
            detail={"category": "BUDGET_LIMIT", "message": "Business finder query budget is exhausted"},
        ) from error
    except db.BusinessFinderRateLimitError as error:
        raise HTTPException(
            status_code=429,
            detail={"category": "RATE_LIMIT", "message": "Business finder request rate is limited"},
        ) from error
    except db.BusinessFinderRequestConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"category": "REQUEST_ID_CONFLICT", "message": "Request ID was reused with different input"},
        ) from error
    except db.BusinessFinderDuplicateRequestError as error:
        category = "SUBMISSION_UNCERTAIN" if error.status == "uncertain" else "DUPLICATE_REQUEST"
        raise HTTPException(
            status_code=409,
            detail={
                "category": category,
                "message": "This request ID already reserved a provider call; it will not be repeated",
            },
        ) from error


def _classification(query: LocationQuery, candidates: list[BusinessCandidate]) -> str:
    if not candidates:
        if query.place_id:
            return "INVALID_PLACE_ID"
        return "BUSINESS_NOT_FOUND"
    if query.place_id:
        place_matches = [
            item for item in candidates
            if item.reference_id == query.place_id
        ]
        if not place_matches:
            return "INVALID_PLACE_ID"
        candidates = place_matches
    if not any(item.name for item in candidates):
        return "BUSINESS_INSUFFICIENT_DATA"
    name_matches = [
        item for item in candidates
        if not query.business_name
        or (
            _normalize(query.business_name)
            and _normalize(item.name)
            and (
                _normalize(query.business_name) in _normalize(item.name)
                or _normalize(item.name) in _normalize(query.business_name)
            )
        )
    ]
    if not name_matches:
        return "BUSINESS_IDENTITY_MISMATCH"
    if query.city or query.state:
        location_known = [
            item for item in name_matches
            if (not query.city or item.city) and (not query.state or item.state)
        ]
        if not location_known:
            return "BUSINESS_INSUFFICIENT_DATA"
        location_matches = [
            item for item in location_known
            if (not query.city or _normalize(item.city) == _normalize(query.city))
            and (not query.state or _state_key(item.state) == _state_key(query.state))
        ]
        if not location_matches:
            return "BUSINESS_LOCATION_MISMATCH"
        if len(location_matches) > 1:
            return "BUSINESS_AMBIGUOUS"
        candidate = location_matches[0]
    else:
        if len(name_matches) > 1:
            return "BUSINESS_AMBIGUOUS"
        candidate = name_matches[0]
    if (
        not candidate.reference_id
        or not candidate.name
        or candidate.lat is None
        or candidate.lng is None
        or not math.isfinite(candidate.lat)
        or not math.isfinite(candidate.lng)
        or not -90 <= candidate.lat <= 90
        or not -180 <= candidate.lng <= 180
    ):
        return "BUSINESS_INSUFFICIENT_DATA"
    return "BUSINESS_LOCATED"


def _recovery_actions() -> dict:
    return {
        "review_submitted_details": True,
        "repeat_search_with_corrected_input": True,
        "provide_google_maps_link_or_place_id": True,
        "request_digital_presence_review": {
            "available": False,
            "crm_integration": "not_configured",
        },
        "statement": (
            "A search with no candidates does not prove that the business lacks "
            "a Google Business Profile."
        ),
    }


def _provider_call_exception(error: ProviderError, request_id: str) -> None:
    db.finish_business_finder_search(
        request_id,
        error.category,
        uncertain=error.uncertain,
    )
    log.warning("business finder provider failure category=%s", error.category)
    status = (
        502
        if error.category in {"AUTHENTICATION_ERROR", "API_CONTRACT_ERROR"}
        else 503
    )
    if error.category == "RATE_LIMIT":
        status = 429
    elif error.category == "BUSINESS_NOT_FOUND":
        status = 404
    elif error.category == "INVALID_PLACE_ID":
        status = 422
    raise HTTPException(
        status_code=status,
        detail={
            "category": error.category,
            "message": {
                "AUTHENTICATION_ERROR": "Provider authentication failed",
                "RATE_LIMIT": "Provider rate limit reached",
                "API_CONTRACT_ERROR": "Provider returned an invalid response",
                "BUSINESS_NOT_FOUND": "The requested business was not found",
                "INVALID_PLACE_ID": "The provider could not resolve this Place ID",
                "PROVIDER_DISABLED": "This provider is not enabled",
                "TEMPORARY_ERROR": "The location provider is temporarily unavailable",
            }.get(error.category, "Location provider request failed"),
            "request_id": request_id,
            "retry_same_request_id": False,
        },
    )


def _unexpected_provider_exception(request_id: str, error: Exception) -> None:
    db.finish_business_finder_search(
        request_id,
        "TEMPORARY_ERROR",
        uncertain=True,
    )
    log.warning("business finder provider adapter failed")
    raise HTTPException(
        status_code=503,
        detail={
            "category": "TEMPORARY_ERROR",
            "message": "Location provider is temporarily unavailable",
            "request_id": request_id,
            "retry_same_request_id": False,
        },
    ) from error


@router.post("/search")
async def search_business(body: BusinessSearchIn, request: Request):
    query = _query_from_input(body)
    if body.request_id.version != 4:
        raise HTTPException(status_code=422, detail={"category": "INVALID_INPUT", "message": "request_id must be UUIDv4"})
    secret = _signing_secret()
    query_hash = _query_hash(query, secret)
    try:
        provider = _provider_from_config()
    except BusinessFinderProviderDisabledError as error:
        raise HTTPException(
            status_code=503,
            detail={"category": "TEMPORARY_ERROR", "message": "Business finder provider is not enabled"},
        ) from error
    except ProviderError as error:
        _provider_call_exception(error, str(body.request_id))
    policy = _policy_for(provider)
    if query.place_id and policy.provider_id != "google_places":
        raise HTTPException(
            status_code=422,
            detail={
                "category": "INVALID_PLACE_ID",
                "message": "Place IDs extracted from Google Maps require the Google Places provider",
            },
        )
    global_rate = (
        (1, max(1, int(os.environ.get("GEOGRID_OSM_GLOBAL_RATE_PER_SECOND", "5"))), 0)
        if policy.provider_id == "osm_nominatim"
        else None
    )
    _reserve_provider_call(
        str(body.request_id),
        query_hash,
        policy.provider_id,
        request,
        secret,
        global_rate=global_rate,
    )
    try:
        candidates = await provider.search(query)
    except ProviderError as error:
        _provider_call_exception(error, str(body.request_id))
    except Exception as error:
        _unexpected_provider_exception(str(body.request_id), error)

    classification = _classification(query, candidates)
    if classification == "INVALID_PLACE_ID":
        candidates = []
    expires_at = int(time.time()) + _TOKEN_TTL_SECONDS
    candidate_payloads = []
    for candidate in candidates[:5]:
        if not candidate.reference_id:
            continue
        nonce = secrets.token_urlsafe(24)
        db.create_business_finder_confirmation(_nonce_hash(nonce), expires_at)
        candidate_payloads.append(
            _candidate_payload(
                candidate,
                policy,
                _candidate_token(
                    provider_id=policy.provider_id,
                    reference_id=candidate.reference_id,
                    request_id=str(body.request_id),
                    query_hash=query_hash,
                    nonce=nonce,
                    stage="candidate",
                    expires_at=expires_at,
                    secret=secret,
                ),
            )
        )
    db.finish_business_finder_search(str(body.request_id), classification)
    return {
        "request_id": str(body.request_id),
        "classification": classification,
        "message": {
            "BUSINESS_LOCATED": "One matching candidate was found; explicit confirmation is required.",
            "BUSINESS_AMBIGUOUS": "Multiple matching candidates were found; choose the exact business.",
            "BUSINESS_NOT_FOUND": "No candidate was returned by this search.",
            "INVALID_PLACE_ID": "The provided Place ID could not be resolved by this provider.",
            "BUSINESS_INSUFFICIENT_DATA": "The provider returned insufficient identity or location data.",
            "BUSINESS_LOCATION_MISMATCH": "The candidate location differs from the submitted location.",
            "BUSINESS_IDENTITY_MISMATCH": "The provider candidates do not match the submitted business name.",
        }.get(classification, "Review the candidates and confirm the exact business."),
        "user_submitted": {
            "business_name": query.business_name,
            "city": query.city,
            "state": query.state,
            "address": query.address,
            "google_maps_url": query.google_maps_url,
            "place_id": query.place_id,
        },
        "candidates": candidate_payloads,
        "confirmation_required": True,
        "diagnostic_allowed": False,
        "alternative_actions": (
            _recovery_actions()
            if classification == "BUSINESS_NOT_FOUND"
            else None
        ),
        "provider_attribution": policy.attribution,
    }


@router.post("/confirm")
def confirm_business(body: BusinessConfirmIn):
    if body.request_id.version != 4:
        raise HTTPException(status_code=422, detail={"category": "INVALID_INPUT", "message": "request_id must be UUIDv4"})
    if body.confirmed is not True:
        raise HTTPException(
            status_code=422,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Explicit identity confirmation is required"},
        )
    secret = _signing_secret()
    claims = _decode_token(body.candidate_token, secret)
    if claims.get("stage") != "candidate":
        raise HTTPException(
            status_code=422,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "A candidate token is required"},
        )
    confirmed_at = int(time.time())
    nonce = hmac.new(
        secret,
        f"confirmed:{claims['nonce']}:{body.request_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expires_at = min(int(claims["expires_at"]), confirmed_at + _TOKEN_TTL_SECONDS)
    try:
        db.confirm_business_finder_candidate(
            _nonce_hash(claims["nonce"]),
            str(body.request_id),
            hashlib.sha256(body.candidate_token.encode("utf-8")).hexdigest(),
            _nonce_hash(nonce),
            expires_at,
            confirmed_at,
        )
    except db.BusinessFinderConfirmationConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"category": "REQUEST_ID_CONFLICT", "message": "Confirmation request ID conflicts with prior data"},
        ) from error
    except db.BusinessFinderConfirmationError as error:
        raise HTTPException(
            status_code=409,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Candidate confirmation was already used or expired"},
        ) from error
    confirmation_token = _candidate_token(
        provider_id=claims["provider_id"],
        reference_id=claims["reference_id"],
        request_id=claims["search_request_id"],
        query_hash=claims["query_hash"],
        nonce=nonce,
        stage="confirmed",
        expires_at=expires_at,
        secret=secret,
    )
    return {
        "request_id": str(body.request_id),
        "business_confirmed": True,
        "confirmation_token": confirmation_token,
        "diagnostic_allowed": False,
        "note": "Confirmation establishes identity only; it does not grant storage or provider-data rights.",
    }


def _source_is_scanner_compatible(policy: ProviderPolicy) -> bool:
    return {"lat", "lng"}.issubset(policy.scanner_permanent_fields)


def _diagnostic_hash(
    body: BusinessDiagnosticIn,
    query: LocationQuery,
    secret: bytes,
) -> str:
    payload = {
        "query_hash": _query_hash(query, secret),
        "keywords": [keyword.strip() for keyword in body.keywords],
        "grid_size": body.grid_size,
        "spacing_km": body.spacing_km,
        "confirmation_token": body.confirmation_token,
    }
    return hmac.new(
        secret,
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@router.post("/diagnostics")
async def start_business_diagnostic(body: BusinessDiagnosticIn, request: Request):
    if body.request_id.version != 4 or body.search.request_id.version != 4:
        raise HTTPException(status_code=422, detail={"category": "INVALID_INPUT", "message": "request IDs must be UUIDv4"})
    if body.confirmed is not True:
        raise HTTPException(
            status_code=422,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Confirm the selected business before diagnosis"},
        )
    query = _query_from_input(body.search)
    secret = _signing_secret()
    claims = _decode_token(body.confirmation_token, secret)
    if (
        claims.get("stage") != "confirmed"
        or claims.get("search_request_id") != str(body.search.request_id)
        or not hmac.compare_digest(
            claims.get("query_hash", ""),
            _query_hash(query, secret),
        )
    ):
        raise HTTPException(
            status_code=409,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Confirmation does not match the submitted business"},
        )
    try:
        provider = _provider_from_config()
    except BusinessFinderProviderDisabledError as error:
        raise HTTPException(
            status_code=503,
            detail={"category": "TEMPORARY_ERROR", "message": "Business finder provider is not available"},
        ) from error
    except ProviderError as error:
        _provider_call_exception(error, str(body.request_id))
    policy = _policy_for(provider)
    if policy.provider_id != claims.get("provider_id") or not _source_is_scanner_compatible(policy):
        raise HTTPException(
            status_code=409,
            detail={
                "category": "SOURCE_NOT_AUTHORIZED",
                "message": "This provider's location data is not authorized for persistent scan history.",
                "provider_id": policy.provider_id,
                "diagnostic_started": False,
            },
        )
    business_name = query.business_name
    if not business_name:
        raise HTTPException(
            status_code=422,
            detail={
                "category": "BUSINESS_INSUFFICIENT_DATA",
                "message": "A user-provided business name is required for the diagnostic",
            },
        )

    diagnostic_hash = _diagnostic_hash(body, query, secret)
    prior = db.get_business_finder_diagnostic(str(body.request_id))
    if prior:
        if prior["request_hash"] != diagnostic_hash:
            raise HTTPException(
                status_code=409,
                detail={"category": "REQUEST_ID_CONFLICT", "message": "Diagnostic request conflicts with prior data"},
            )
        return {
            "request_id": str(body.request_id),
            "scan_ids": prior["scan_ids"],
            "created_new": False,
            "status": "queued",
        }

    keywords = [keyword.strip() for keyword in body.keywords]
    if any(len(keyword) < 2 for keyword in keywords):
        raise HTTPException(
            status_code=422,
            detail={"category": "INVALID_INPUT", "message": "Keywords must contain at least two characters"},
        )
    global_rate = (
        (1, max(1, int(os.environ.get("GEOGRID_OSM_GLOBAL_RATE_PER_SECOND", "5"))), 0)
        if policy.provider_id == "osm_nominatim"
        else None
    )
    _reserve_provider_call(
        str(body.request_id),
        diagnostic_hash,
        policy.provider_id,
        request,
        secret,
        global_rate=global_rate,
    )
    try:
        candidate = await provider.resolve(claims["reference_id"])
    except ProviderError as error:
        _provider_call_exception(error, str(body.request_id))
    except Exception as error:
        _unexpected_provider_exception(str(body.request_id), error)
    if candidate is None:
        db.finish_business_finder_search(str(body.request_id), "BUSINESS_NOT_FOUND")
        raise HTTPException(
            status_code=404,
            detail={"category": "BUSINESS_NOT_FOUND", "message": "The selected business is no longer available from the provider"},
        )
    if (
        candidate.reference_id != claims["reference_id"]
        or candidate.lat is None
        or candidate.lng is None
        or not math.isfinite(candidate.lat)
        or not math.isfinite(candidate.lng)
        or not -90 <= candidate.lat <= 90
        or not -180 <= candidate.lng <= 180
    ):
        db.finish_business_finder_search(str(body.request_id), "BUSINESS_INSUFFICIENT_DATA")
        raise HTTPException(
            status_code=422,
            detail={"category": "BUSINESS_INSUFFICIENT_DATA", "message": "Provider could not revalidate the selected location"},
        )
    candidate_classification = _classification(query, [candidate])
    if candidate_classification not in {
        "BUSINESS_LOCATED",
        "BUSINESS_AMBIGUOUS",
    }:
        db.finish_business_finder_search(
            str(body.request_id),
            candidate_classification,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "category": candidate_classification,
                "message": "The selected business no longer matches the submitted identity or location",
            },
        )

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    provenance = [
        {
            "field_name": "name",
            "data_origin": "user_submitted",
            "provider_id": "user_input",
            "license_name": "user-provided business information",
            "retrieved_at": retrieved_at,
            "storage_authorized": True,
        }
    ]
    address = ", ".join(
        value for value in (query.address, query.city, query.state) if value
    ) or None
    if address:
        provenance.append(
            {
                "field_name": "address",
                "data_origin": "user_submitted",
                "provider_id": "user_input",
                "license_name": "user-provided business information",
                "retrieved_at": retrieved_at,
                "storage_authorized": True,
            }
        )
    for field in ("lat", "lng"):
        provenance.append(
            {
                "field_name": field,
                "data_origin": "provider",
                "provider_id": policy.provider_id,
                "license_name": policy.license_name,
                "retrieved_at": retrieved_at,
                "valid_until": None,
                "storage_authorized": field in policy.permanent_storage_fields,
                "attribution": policy.attribution,
            }
        )
    place_id = candidate.place_id
    if place_id and "place_id" in policy.permanent_storage_fields:
        provenance.append(
            {
                "field_name": "place_id",
                "data_origin": "provider",
                "provider_id": policy.provider_id,
                "license_name": policy.license_name,
                "retrieved_at": retrieved_at,
                "valid_until": None,
                "storage_authorized": True,
                "attribution": policy.attribution,
            }
        )
    business = {
        "name": business_name,
        "address": address,
        "lat": candidate.lat,
        "lng": candidate.lng,
        "place_id": place_id if "place_id" in policy.permanent_storage_fields else None,
        "provider_id": policy.provider_id,
        "provider_reference_id": (
            candidate.reference_id
            if (
                "provider_reference_id" in policy.permanent_storage_fields
                or "place_id" in policy.permanent_storage_fields
            )
            else None
        ),
        "provider_reference_storage_authorized": True,
        "license_name": policy.license_name,
        "retrieved_at": retrieved_at,
        "attribution": policy.attribution,
    }
    try:
        result = db.create_business_finder_diagnostic(
            request_id=str(body.request_id),
            request_hash=diagnostic_hash,
            confirmation_nonce_hash=_nonce_hash(claims["nonce"]),
            current_time=int(time.time()),
            business=business,
            keywords=keywords,
            grid_size=body.grid_size,
            spacing_km=body.spacing_km,
            provenance=provenance,
        )
    except db.BusinessFinderConfirmationError as error:
        raise HTTPException(
            status_code=409,
            detail={"category": "CONFIRMATION_REQUIRED", "message": "Confirmation token is expired or already used"},
        ) from error
    except db.BusinessFinderDiagnosticConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"category": "REQUEST_ID_CONFLICT", "message": "Diagnostic request conflicts with prior data"},
        ) from error
    except ValueError as error:
        db.finish_business_finder_search(str(body.request_id), "API_CONTRACT_ERROR")
        raise HTTPException(
            status_code=422,
            detail={"category": "API_CONTRACT_ERROR", "message": str(error)},
        ) from error
    db.finish_business_finder_search(str(body.request_id), "BUSINESS_LOCATED")
    if result["created_new"]:
        from . import main

        import asyncio

        asyncio.create_task(main._run_queued(result["scan_ids"]))
    return {
        "request_id": str(body.request_id),
        "scan_ids": result["scan_ids"],
        "created_new": result["created_new"],
        "status": "queued",
        "data_provenance": {
            "provider_id": policy.provider_id,
            "license_name": policy.license_name,
            "attribution": policy.attribution,
            "permanent_scan_storage_authorized": True,
        },
    }
