#!/usr/bin/env python3
"""Verify an authorized historical SQLite backup on an isolated copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def _readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _user_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        )
    ]


def _encoded_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    return value


def _table_digest(
    connection: sqlite3.Connection, table: str, columns: list[str]
) -> tuple[int, str]:
    quoted_table = _quote_identifier(table)
    quoted_columns = [_quote_identifier(column) for column in columns]
    selection = ", ".join(quoted_columns) if quoted_columns else "rowid"
    table_info = connection.execute(
        f"PRAGMA table_info({_quote_identifier(table)})"
    ).fetchall()
    primary_key = [
        row["name"]
        for row in sorted(table_info, key=lambda item: item["pk"])
        if row["pk"]
    ]
    ordering_columns = primary_key or ["rowid"]
    ordering = ", ".join(_quote_identifier(column) for column in ordering_columns)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(
        f"SELECT {selection} FROM {quoted_table} ORDER BY {ordering}"
    ):
        encoded = json.dumps(
            [_encoded_value(value) for value in row],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def _schema_signature(connection: sqlite3.Connection) -> list[tuple[str, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') "
            "FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
    ]


def _integrity(connection: sqlite3.Connection) -> dict[str, object]:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_key_violations = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_key_violations),
    }


def _snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    tables = {}
    for table in _user_tables(connection):
        columns = _columns(connection, table)
        row_count, digest = _table_digest(connection, table, columns)
        tables[table] = {
            "columns": columns,
            "row_count": row_count,
            "digest": digest,
        }
    return {"schema": _schema_signature(connection), "tables": tables}


def _json_records(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _json_records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _json_records(nested)


def _contact_counts(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    json_sources = (("points", "results"), ("scans", "competitors"))
    contact_keys = ("phone", "phone_number", "telephone")
    website_keys = ("website", "url")
    position_keys = ("position", "rank_group", "rank_absolute")

    for table, column in json_sources:
        if (
            table not in _user_tables(connection)
            or column not in _columns(connection, table)
        ):
            continue
        values = {
            "candidate_records": 0,
            "candidates_with_phone": 0,
            "candidates_with_website": 0,
            "candidates_with_provider_position": 0,
        }
        for row in connection.execute(
            f"SELECT {_quote_identifier(column)} FROM {_quote_identifier(table)}"
        ):
            try:
                payload = json.loads(row[0]) if row[0] else None
            except (TypeError, json.JSONDecodeError):
                continue
            for record in _json_records(payload):
                if not any(
                    key in record
                    for key in ("name", "title", *contact_keys, *website_keys)
                ):
                    continue
                values["candidate_records"] += 1
                if any(record.get(key) not in (None, "") for key in contact_keys):
                    values["candidates_with_phone"] += 1
                if any(record.get(key) not in (None, "") for key in website_keys):
                    values["candidates_with_website"] += 1
                if any(record.get(key) not in (None, "") for key in position_keys):
                    values["candidates_with_provider_position"] += 1
        counts[f"{table}.{column}"] = values

    if "commercial_prospects" in _user_tables(connection):
        columns = set(_columns(connection, "commercial_prospects"))
        available = [field for field in ("phone", "website") if field in columns]
        values = {
            "records": 0,
            "phone_column_present": int("phone" in columns),
            "website_column_present": int("website" in columns),
            "with_phone": 0,
            "with_website": 0,
        }
        selection = ", ".join(_quote_identifier(field) for field in available)
        if selection:
            for row in connection.execute(
                f"SELECT {selection} FROM commercial_prospects"
            ):
                values["records"] += 1
                if "phone" in columns:
                    values["with_phone"] += int(row["phone"] not in (None, ""))
                if "website" in columns:
                    values["with_website"] += int(row["website"] not in (None, ""))
        else:
            values["records"] = connection.execute(
                "SELECT COUNT(*) FROM commercial_prospects"
            ).fetchone()[0]
        counts["commercial_prospects"] = values

    if "commercial_prospect_observations" in _user_tables(connection):
        columns = set(_columns(connection, "commercial_prospect_observations"))
        if "provider_position" in columns:
            rows = connection.execute(
                "SELECT COUNT(*) AS records, "
                "SUM(CASE WHEN provider_position IS NOT NULL THEN 1 ELSE 0 END) "
                "AS with_provider_position FROM commercial_prospect_observations"
            ).fetchone()
            counts["commercial_prospect_observations"] = {
                "records": rows["records"],
                "with_provider_position": rows["with_provider_position"] or 0,
            }
    return counts


def _preservation_errors(
    before: dict[str, object],
    after: dict[str, object],
    connection: sqlite3.Connection,
) -> list[str]:
    errors = []
    before_tables = before["tables"]
    after_tables = after["tables"]
    for table, original in before_tables.items():
        if table not in after_tables:
            errors.append(f"table_removed:{table}")
            continue
        current = after_tables[table]
        if not set(original["columns"]).issubset(current["columns"]):
            errors.append(f"column_removed:{table}")
            continue
        count, digest = _table_digest(connection, table, original["columns"])
        if count != original["row_count"] or digest != original["digest"]:
            errors.append(f"data_changed:{table}")
    return errors


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only compare a historical SQLite backup with isolated migrations."
    )
    parser.add_argument("backup", type=Path, help="Authorized, consistent SQLite backup")
    args = parser.parse_args()
    source = args.backup.resolve()
    if not source.is_file():
        parser.error("backup path must be an existing file")

    with tempfile.TemporaryDirectory(prefix="geogrid-historical-") as workdir:
        migrated_copy = Path(workdir) / "migration-copy.sqlite3"
        with closing(_readonly(source)) as source_connection:
            before_integrity = _integrity(source_connection)
            if before_integrity["integrity_check"] != ["ok"]:
                raise RuntimeError("Backup integrity_check failed")
            if before_integrity["foreign_key_violations"]:
                raise RuntimeError("Backup foreign_key_check found violations")
            before = _snapshot(source_connection)
            core_tables = ("businesses", "scans", "points")
            if any(
                name not in before["tables"]
                or before["tables"][name]["row_count"] == 0
                for name in core_tables
            ):
                raise RuntimeError(
                    "Backup must contain populated businesses, scans, and points tables"
                )
            contacts = _contact_counts(source_connection)
            with closing(sqlite3.connect(migrated_copy)) as destination:
                source_connection.backup(destination)

        os.environ["GEOGRID_DB"] = str(migrated_copy)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import db

        db.init_db()
        with closing(_readonly(migrated_copy)) as connection:
            after_integrity = _integrity(connection)
            after_first = _snapshot(connection)
            errors = _preservation_errors(before, after_first, connection)
        if after_integrity["integrity_check"] != ["ok"]:
            errors.append("post_migration_integrity_check_failed")
        if after_integrity["foreign_key_violations"]:
            errors.append("post_migration_foreign_key_check_failed")

        db.init_db()
        with closing(_readonly(migrated_copy)) as connection:
            after_second = _snapshot(connection)
        idempotent = after_first == after_second

        summary = {
            "source_open_mode": "read-only",
            "migration_target": "temporary copy",
            "before": {
                **before_integrity,
                "tables": {
                    name: table["row_count"]
                    for name, table in before["tables"].items()
                },
                "schema": {
                    name: table["columns"]
                    for name, table in before["tables"].items()
                },
                "contact_field_observations": contacts,
            },
            "after_migrations": {
                **after_integrity,
                "tables": {
                    name: table["row_count"]
                    for name, table in after_first["tables"].items()
                },
                "schema": {
                    name: table["columns"]
                    for name, table in after_first["tables"].items()
                },
                "added_tables": sorted(
                    set(after_first["tables"]) - set(before["tables"])
                ),
                "added_columns": {
                    name: sorted(
                        set(after_first["tables"][name]["columns"])
                        - set(table["columns"])
                    )
                    for name, table in before["tables"].items()
                    if name in after_first["tables"]
                    and set(after_first["tables"][name]["columns"])
                    - set(table["columns"])
                },
            },
            "legacy_data_preserved": not any(
                error.startswith(
                    ("table_removed:", "column_removed:", "data_changed:")
                )
                for error in errors
            ),
            "preservation_errors": errors,
            "second_initialization_idempotent": idempotent,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not errors and idempotent else 1


if __name__ == "__main__":
    raise SystemExit(_main())
