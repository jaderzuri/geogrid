# Smart Business Finder — P04

## Status and scope

P04 adds backend search, candidate confirmation, and diagnostic creation. It
does not change the Landing Page UI, add a CRM contract, or enable a provider by
default. The daily query limit defaults to zero. Tests use fake providers and
make no external requests.

P04.1 is a release gate: review the chosen provider's license, permitted uses,
retention, attribution, quotas, and compatibility with persistent Smart Grid
coordinates before enabling this flow or publishing the Landing Page 2.0.
Provider policy pages can change; revalidate them at that time.

## API

All routes are under `/api/business-finder`. Responses include `Cache-Control:
no-store` and `Referrer-Policy` protections. Request IDs must be UUIDv4.

### `POST /search`

Provide either a Google Place ID / supported Google Maps URL, or a business
name, city, and state. Address is optional. Inputs reject unknown fields.

```json
{
  "request_id": "UUIDv4",
  "business_name": "Café Lume",
  "city": "São Paulo",
  "state": "SP",
  "address": "Rua Exemplo, 100"
}
```

The response contains `classification`, submitted input, up to five transient
`candidates`, `data_provenance`, and `confirmation_required`. Candidate
information can include commercial name, address, city/state, category, Place
ID, coordinates, and map URL when returned by the provider. A signed
`candidate_token` carries only provider/reference identifiers, request/query
hashes, a nonce, stage, and expiry; it does not contain names, addresses, or
coordinates.

Possible classifications include `BUSINESS_LOCATED`,
`BUSINESS_AMBIGUOUS`, `BUSINESS_NOT_FOUND`,
`BUSINESS_INSUFFICIENT_DATA`, `BUSINESS_LOCATION_MISMATCH`, and
`BUSINESS_IDENTITY_MISMATCH`. Technical provider failures instead use
`TEMPORARY_ERROR`, `AUTHENTICATION_ERROR`, `RATE_LIMIT`, or
`API_CONTRACT_ERROR`. A missing or mismatched Place ID is reported as
`INVALID_PLACE_ID` and yields no confirmable candidates. Place IDs are only
accepted with the Google Places provider. No result is automatically selected. An empty result
does not establish that the business lacks a Google Business Profile.

### `POST /confirm`

Confirmation is an explicit user assertion of identity, not permission to
store provider data. It is single-use, replayable only with the same request ID
and same candidate token, and returns a short-lived `confirmation_token`.
Reusing a confirmation request ID for another token returns
`REQUEST_ID_CONFLICT`.

```json
{
  "request_id": "UUIDv4",
  "candidate_token": "<candidate_token from /search>",
  "confirmed": true
}
```

The response sets `business_confirmed: true` and
`diagnostic_allowed: false`. A separate diagnostic request is still required.

### `POST /diagnostics`

Requires explicit confirmation, the original search request and matching
confirmation token, one to ten keywords, and a valid grid configuration.
Current defaults are a 3×3 grid and 1 km spacing. The selected provider must
authorize permanent storage of scanner coordinates; otherwise the request is
rejected before creating scans.

Google Places identity confirmation cannot start a persistent Smart Grid
diagnostic: its coordinates are not authorized for the grid's point-in-polygon
analysis. A Place ID-only search therefore remains a transient lookup. The
current OSM path must be searched and confirmed through its own configured
provider; cross-provider identity transfer is not implemented.

```json
{
  "request_id": "UUIDv4",
  "confirmation_token": "<confirmation_token from /confirm>",
  "confirmed": true,
  "search": {
    "request_id": "same UUIDv4 used for /search",
    "business_name": "Café Lume",
    "city": "São Paulo",
    "state": "SP",
    "address": "Rua Exemplo, 100"
  },
  "keywords": ["cafeteria"],
  "grid_size": 3,
  "spacing_km": 1.0
}
```

The response returns `scan_ids`, `created_new`, and `status: "queued"`.
Repeating an identical request returns the original scan IDs; changing data
under an existing request ID returns `REQUEST_ID_CONFLICT`. This endpoint
re-resolves the selected provider reference immediately before creating scans.

## Provider and data-use gates

| Provider | Current behavior | Persistent scanner use |
| --- | --- | --- |
| Google Places | Adapter available only when explicitly configured. Candidate content is returned transiently; Place ID is the only provider field allowed for persistent storage. | Blocked. Google policy prohibits using Places latitude/longitude as input to point-in-polygon analysis. |
| Nominatim public service | Explicitly blocked; the code does not call `nominatim.openstreetmap.org`. | Not applicable. |
| Configured Nominatim instance | Adapter accepts HTTPS, a non-public Nominatim host, and a configured User-Agent. | Only enabled when both `GEOGRID_OSM_ODBL_STORAGE_ACCEPTED` and `GEOGRID_OSM_ATTRIBUTION_READY` are true. This is an operational gate, not legal advice or proof that the deployment complies. |
| MapTiler / other providers | No active adapter or account configured by P04. | Requires separate implementation, terms review, quota/cost controls, and P04.1 approval. |

Search inputs, candidate names/addresses/coordinates, and provider responses
are not stored in the new request ledger. The ledger persists request IDs,
keyed hashes, provider/classification/status, rate/budget counters, and hashed
confirmation nonces. Diagnostic records store the user-submitted business
details, authorized coordinates, provider reference where licensed, and
field-level provenance as required to run the existing scanner. Existing
businesses are not overwritten on identifier conflict.

## Configuration

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `GEOGRID_BUSINESS_FINDER_PROVIDER` | `google_places` or `osm_nominatim`; unset/unknown disables search | unset |
| `GEOGRID_BUSINESS_FINDER_SIGNING_SECRET` | HMAC signing secret; must contain at least 32 characters | unset (requests fail closed) |
| `GEOGRID_BUSINESS_FINDER_DAILY_QUERY_LIMIT` | Maximum provider calls per UTC day; both searches and diagnostic re-resolution count | `0` |
| `GEOGRID_BUSINESS_FINDER_RATE_LIMIT_PER_MINUTE` | Per-caller request limit | `10` |
| `GEOGRID_GOOGLE_PLACES_API_KEY` | Backend-only Google Places credential | unset |
| `GEOGRID_NOMINATIM_BASE_URL` | HTTPS URL of a non-public Nominatim instance | unset |
| `GEOGRID_NOMINATIM_USER_AGENT` | Identifying User-Agent for the configured instance | unset |
| `GEOGRID_OSM_GLOBAL_RATE_PER_SECOND` | Aggregate rate limit for configured Nominatim requests | `5` |
| `GEOGRID_OSM_ODBL_STORAGE_ACCEPTED` | Explicit operator acceptance gate for ODbL storage | false |
| `GEOGRID_OSM_ATTRIBUTION_READY` | Explicit operator assertion that required attribution is ready | false |

The daily limit counts requests, not currency. P04 does not implement a
provider-price estimator or financial spend ceiling; do not treat this counter
as a monetary budget.

## Official policy references

- [Google Places API policies](https://developers.google.com/maps/documentation/places/web-service/policies): storage restrictions, Place ID exception, coordinate caching exception, and prohibited point-in-polygon use.
- [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/): applies to the public OSMF service, including its 1 request/second ceiling, attribution, and limits on bulk/systematic use. P04 blocks that public host.
- [OpenStreetMap copyright and ODbL](https://www.openstreetmap.org/copyright): attribution and share-alike requirements for OSM data.
- [MapTiler Cloud terms](https://www.maptiler.com/terms/cloud/): any later MapTiler use must be evaluated against the current plan-specific terms and quotas. P04 does not activate it.

## Validation evidence

`tests/test_business_finder.py` uses fake providers and temporary SQLite
databases. The focused run passed 25 tests covering candidate classification,
input validation, transport/status mapping, rate/budget limits, duplicate
searches, confirmation replay/conflict/expiry, provenance, provider gates,
scanner authorization, and diagnostic idempotency. This is unit/API testing;
it is not evidence of live provider behavior or production compliance.
