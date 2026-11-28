"""Capture and export eligibility for businesses already present in completed scans."""

import hashlib
import json
import math
import unicodedata

from . import db
from .commercial import prospecting_storage_approved


_LICENSE_LABELS = {
    "simulator": "synthetic-only",
    "dataforseo_standard": "operator-approved-use",
}


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold().strip()


def _candidate_identity(provider: str, candidate: dict) -> tuple[str, str] | None:
    cid = candidate.get("cid")
    if isinstance(cid, str) and cid.strip():
        kind, value = "cid", cid.strip().casefold()
    else:
        place_id = candidate.get("place_id")
        if isinstance(place_id, str) and place_id.strip():
            kind, value = "place_id", place_id.strip().casefold()
        else:
            name = candidate.get("name")
            if not isinstance(name, str) or not name.strip():
                return None
            address = candidate.get("address")
            if isinstance(address, str) and address.strip():
                kind = "name_address"
                value = f"{_normalized(name)}|{_normalized(address)}"
            else:
                lat, lng = candidate.get("lat"), candidate.get("lng")
                if (
                    not isinstance(lat, (int, float))
                    or isinstance(lat, bool)
                    or not isinstance(lng, (int, float))
                    or isinstance(lng, bool)
                    or not math.isfinite(lat)
                    or not math.isfinite(lng)
                ):
                    return None
                kind = "name_location"
                value = f"{_normalized(name)}|{lat:.3f}|{lng:.3f}"
    digest = hashlib.sha256(f"{provider}|{kind}|{value}".encode("utf-8")).hexdigest()
    return kind, digest


def _target_match(candidate: dict, point: dict, target: dict) -> bool:
    cid = candidate.get("cid")
    place_id = candidate.get("place_id")
    if cid and target.get("cid") and cid == target["cid"]:
        return True
    if place_id and target.get("place_id") and place_id == target["place_id"]:
        return True
    if (
        point.get("rank") is not None
        and candidate.get("_result_rank") == point["rank"]
        and _normalized(str(candidate.get("name") or ""))
        == _normalized(str(target.get("name") or ""))
    ):
        return True
    return False


def _qualification(data_mode: str, observations: int, best_rank: int | None) -> str:
    if data_mode == "synthetic":
        return "simulation"
    if observations >= 3 or (best_rank is not None and best_rank <= 3):
        return "priority"
    if best_rank is not None and best_rank <= 10:
        return "standard"
    return "low"


def capture_completed_scan(scan_id: int) -> dict:
    """Idempotently retain eligible competitors from a completed commercial Express scan."""

    scan = db.get_scan(scan_id)
    if not scan or scan["status"] != "done":
        return {"captured": 0, "reason": "scan_not_done"}
    provider = str(scan.get("result_provider") or "unknown")
    if provider not in _LICENSE_LABELS:
        return {"captured": 0, "reason": "provider_not_eligible"}
    if provider == "dataforseo_standard" and not prospecting_storage_approved():
        return {"captured": 0, "reason": "prospecting_use_not_approved"}

    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        diagnostic = connection.execute(
            "SELECT d.id, d.lead_id, d.product_id, "
            "b.name AS target_name, b.cid AS target_cid, b.place_id AS target_place_id "
            "FROM commercial_diagnostics d "
            "JOIN commercial_leads l ON l.id = d.lead_id "
            "JOIN businesses b ON b.id = d.business_id "
            "WHERE d.scan_id = ?",
            (scan_id,),
        ).fetchone()
        if not diagnostic or diagnostic["product_id"] != "express":
            return {"captured": 0, "reason": "not_commercial_express"}

        data_mode = "synthetic" if provider == "simulator" else "authorized"
        export_eligible = int(
            data_mode == "authorized" and prospecting_storage_approved()
        )
        observed_at = scan.get("finished") or db.now()
        grouped: dict[str, dict] = {}
        for point in scan.get("points", []):
            if point["status"] != "ok":
                continue
            by_identity: dict[str, dict] = {}
            point_results = point["results"]
            if isinstance(point_results, str):
                point_results = json.loads(point_results or "[]")
            for result_rank, result in enumerate(
                point_results, start=1
            ):
                if not isinstance(result, dict):
                    continue
                candidate = {**result, "_result_rank": result_rank}
                if _target_match(
                    candidate,
                    point,
                    {
                        "cid": diagnostic["target_cid"],
                        "place_id": diagnostic["target_place_id"],
                        "name": diagnostic["target_name"],
                    },
                ):
                    continue
                identity = _candidate_identity(provider, candidate)
                if identity is None:
                    continue
                identity_type, identity_hash = identity
                existing = by_identity.get(identity_hash)
                if existing and existing["result_rank"] <= result_rank:
                    continue
                by_identity[identity_hash] = {
                    "identity_type": identity_type,
                    "identity_hash": identity_hash,
                    "name": str(result.get("name", "")).strip()[:200],
                    "phone": (
                        result.get("phone").strip()
                        if isinstance(result.get("phone"), str)
                        and result.get("phone").strip()
                        else None
                    ),
                    "website": (
                        result.get("url").strip()
                        if isinstance(result.get("url"), str)
                        and result.get("url").strip()
                        else None
                    ),
                    "cid": result.get("cid"),
                    "place_id": result.get("place_id"),
                    "address": result.get("address"),
                    "category": result.get("category"),
                    "lat": result.get("lat"),
                    "lng": result.get("lng"),
                    "result_rank": result_rank,
                    "provider_position": (
                        result["position"]
                        if isinstance(result.get("position"), int)
                        and not isinstance(result.get("position"), bool)
                        and result["position"] > 0
                        else None
                    ),
                    "point_row": point["row"],
                    "point_col": point["col"],
                }
            for candidate in by_identity.values():
                current = grouped.get(candidate["identity_hash"])
                if current is None:
                    grouped[candidate["identity_hash"]] = {
                        **candidate,
                        "observations": [
                            (
                                candidate["point_row"],
                                candidate["point_col"],
                                candidate["result_rank"],
                                candidate["provider_position"],
                            )
                        ],
                    }
                else:
                    current["observations"].append(
                        (
                            candidate["point_row"],
                            candidate["point_col"],
                            candidate["result_rank"],
                            candidate["provider_position"],
                        )
                    )
                    for key in ("phone", "website"):
                        if not current[key] and candidate[key]:
                            current[key] = candidate[key]
                    if candidate["result_rank"] < current["result_rank"]:
                        for key in (
                            "name",
                            "phone",
                            "website",
                            "cid",
                            "place_id",
                            "address",
                            "category",
                            "lat",
                            "lng",
                            "result_rank",
                        ):
                            current[key] = candidate[key]

        captured = 0
        new_candidates = 0
        previously_known = 0
        for candidate in grouped.values():
            existing = connection.execute(
                "SELECT * FROM commercial_prospects WHERE identity_hash = ?",
                (candidate["identity_hash"],),
            ).fetchone()
            if existing:
                previously_known += 1
            else:
                new_candidates += 1
            best_rank = min(
                result_rank
                for _, _, result_rank, _ in candidate["observations"]
            )
            connection.execute(
                "INSERT INTO commercial_prospects ("
                "identity_hash, identity_type, source_provider, source_license, "
                "data_mode, storage_authorized, export_eligible, name, phone, website, "
                "cid, place_id, address, category, lat, lng, first_seen_at, last_seen_at, "
                "observation_count, best_rank, qualification"
                ") VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(identity_hash) DO UPDATE SET "
                "last_seen_at = excluded.last_seen_at, "
                "best_rank = CASE WHEN commercial_prospects.best_rank IS NULL "
                "THEN excluded.best_rank WHEN excluded.best_rank IS NULL "
                "THEN commercial_prospects.best_rank ELSE MIN(commercial_prospects.best_rank, excluded.best_rank) END, "
                "export_eligible = MAX(commercial_prospects.export_eligible, excluded.export_eligible), "
                "phone = COALESCE(excluded.phone, commercial_prospects.phone), "
                "website = COALESCE(excluded.website, commercial_prospects.website), "
                "address = COALESCE(commercial_prospects.address, excluded.address), "
                "category = COALESCE(commercial_prospects.category, excluded.category), "
                "lat = COALESCE(commercial_prospects.lat, excluded.lat), "
                "lng = COALESCE(commercial_prospects.lng, excluded.lng)",
                (
                    candidate["identity_hash"],
                    candidate["identity_type"],
                    provider,
                    _LICENSE_LABELS[provider],
                    data_mode,
                    export_eligible,
                    candidate["name"],
                    candidate["phone"],
                    candidate["website"],
                    candidate["cid"],
                    candidate["place_id"],
                    candidate["address"],
                    candidate["category"],
                    candidate["lat"],
                    candidate["lng"],
                    observed_at,
                    observed_at,
                    best_rank,
                    _qualification(data_mode, 0, best_rank),
                ),
            )
            prospect = connection.execute(
                "SELECT id FROM commercial_prospects WHERE identity_hash = ?",
                (candidate["identity_hash"],),
            ).fetchone()
            for point_row, point_col, result_rank, provider_position in candidate["observations"]:
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO commercial_prospect_observations ("
                    "prospect_id, lead_id, diagnostic_id, scan_id, keyword, point_row, "
                    "point_col, result_rank, provider_position, record_status, "
                    "source_provider, observed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        prospect["id"],
                        diagnostic["lead_id"],
                        diagnostic["id"],
                        scan_id,
                        scan["keyword"],
                        point_row,
                        point_col,
                        result_rank,
                        provider_position,
                        "previously_known" if existing else "new",
                        provider,
                        observed_at,
                    ),
                )
                if inserted.rowcount:
                    captured += 1
                    connection.execute(
                        "UPDATE commercial_prospects SET observation_count = "
                        "observation_count + 1 WHERE id = ?",
                        (prospect["id"],),
                    )
            updated_prospect = connection.execute(
                "SELECT observation_count, best_rank FROM commercial_prospects WHERE id = ?",
                (prospect["id"],),
            ).fetchone()
            connection.execute(
                "UPDATE commercial_prospects SET qualification = ? WHERE id = ?",
                (
                    _qualification(
                        data_mode,
                        updated_prospect["observation_count"],
                        updated_prospect["best_rank"],
                    ),
                    prospect["id"],
                ),
            )
        return {
            "captured": captured,
            "unique_candidates": len(grouped),
            "new_candidates": new_candidates,
            "previously_known": previously_known,
            "data_mode": data_mode,
            "export_eligible": bool(export_eligible),
        }


def list_prospects(
    *,
    query: str = "",
    qualification: str | None = None,
    provider: str | None = None,
    export_eligible: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    clauses = []
    parameters: list[object] = []
    if query:
        clauses.append("(p.name LIKE ? ESCAPE '\\' OR p.address LIKE ? ESCAPE '\\')")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameters.extend((f"%{escaped}%", f"%{escaped}%"))
    if qualification:
        clauses.append("p.qualification = ?")
        parameters.append(qualification)
    if provider:
        clauses.append("p.source_provider = ?")
        parameters.append(provider)
    if export_eligible is not None:
        clauses.append("p.export_eligible = ?")
        parameters.append(int(export_eligible))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect() as connection:
        items = [
            dict(row)
            for row in connection.execute(
                "SELECT p.*, COUNT(DISTINCT o.diagnostic_id) AS diagnostics_seen, "
                "(SELECT latest.record_status FROM commercial_prospect_observations latest "
                "WHERE latest.prospect_id = p.id "
                "ORDER BY latest.observed_at DESC, latest.id DESC LIMIT 1) AS record_status "
                "FROM commercial_prospects p LEFT JOIN commercial_prospect_observations o "
                f"ON o.prospect_id = p.id {where} "
                "GROUP BY p.id ORDER BY p.last_seen_at DESC, p.id DESC LIMIT ?",
                (*parameters, limit),
            )
        ]
        _attach_provider_positions(connection, items)
        return items


def list_leads(
    *,
    query: str = "",
    marketing_consent: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    clauses = []
    parameters: list[object] = []
    if query:
        clauses.append("(business_name LIKE ? ESCAPE '\\' OR city LIKE ? ESCAPE '\\' OR contact_name LIKE ? ESCAPE '\\')")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameters.extend((f"%{escaped}%", f"%{escaped}%", f"%{escaped}%"))
    if marketing_consent is not None:
        clauses.append("marketing_consent = ?")
        parameters.append(int(marketing_consent))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM commercial_leads "
                f"{where} ORDER BY created_at DESC, id DESC LIMIT ?",
                (*parameters, limit),
            )
        ]


def exportable_prospects() -> list[dict]:
    if not prospecting_storage_approved():
        return []
    with db.connect() as connection:
        items = [
            dict(row)
            for row in connection.execute(
                "SELECT id, name, phone, website, address, category, place_id, cid, "
                "first_seen_at, last_seen_at, observation_count, best_rank, qualification, "
                "source_provider, (SELECT latest.record_status "
                "FROM commercial_prospect_observations latest "
                "WHERE latest.prospect_id = commercial_prospects.id "
                "ORDER BY latest.observed_at DESC, latest.id DESC LIMIT 1) AS record_status "
                "FROM commercial_prospects "
                "WHERE storage_authorized = 1 AND export_eligible = 1 "
                "AND data_mode = 'authorized' "
                "ORDER BY last_seen_at DESC, name"
            )
        ]
        _attach_provider_positions(connection, items)
        for item in items:
            item["best_rank_in_any_point"] = item["best_rank"]
        return items


def _attach_provider_positions(connection, items: list[dict]) -> None:
    if not items:
        return
    by_id = {item["id"]: item for item in items}
    ids = list(by_id)
    for offset in range(0, len(ids), 500):
        batch = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        observations = connection.execute(
            "SELECT prospect_id, point_row, point_col, provider_position "
            "FROM commercial_prospect_observations "
            f"WHERE prospect_id IN ({placeholders}) AND provider_position IS NOT NULL "
            "ORDER BY point_row, point_col, id",
            batch,
        )
        positions: dict[int, list[dict[str, int]]] = {}
        for observation in observations:
            positions.setdefault(observation["prospect_id"], []).append(
                {
                    "point_row": observation["point_row"],
                    "point_col": observation["point_col"],
                    "position": observation["provider_position"],
                }
            )
        for prospect_id, item in by_id.items():
            item["provider_positions_by_point"] = json.dumps(
                positions.get(prospect_id, []),
                ensure_ascii=False,
                separators=(",", ":"),
            )


def exportable_leads() -> list[dict]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT business_name, city, state, contact_name, email, whatsapp, "
                "campaign_id, created_at, marketing_consent "
                "FROM commercial_leads WHERE privacy_acknowledged = 1 "
                "AND marketing_consent = 1 ORDER BY created_at DESC"
            )
        ]
