"""Local-only HTTP simulators for the no-billing integration environment."""

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


_lock = threading.Lock()
_tasks: dict[str, dict] = {}
_counts = {"task_post": 0, "task_get": 0, "places": 0, "crm_events": 0}
_crm_db = os.environ.get("MOCK_CRM_DB", "/tmp/mock-crm.sqlite")


def _json_response(handler: BaseHTTPRequestHandler, status: int, value: dict) -> None:
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict | list:
    size = int(handler.headers.get("Content-Length", "0"))
    if size < 1 or size > 1_048_576:
        raise ValueError("Invalid request body size")
    return json.loads(handler.rfile.read(size))


class MockHandler(BaseHTTPRequestHandler):
    server_version = "GeoGridHomologMock/1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/__mock__/failure/timeout":
            time.sleep(2)
            self.close_connection = True
            return
        if path.startswith("/__mock__/failure/"):
            code = int(path.rsplit("/", 1)[-1])
            _json_response(self, code, {"error": {"code": code, "status": "SYNTHETIC_FAILURE"}})
            return
        if path == "/__mock__/metrics":
            with _lock:
                counts = dict(_counts)
                tasks = len(_tasks)
            _json_response(self, 200, {"counts": counts, "tasks": tasks})
            return
        if path.startswith("/v3/serp/google/maps/task_get/advanced/"):
            self._task_get(unquote(path.rsplit("/", 1)[-1]))
            return
        if path.startswith("/v1/places/"):
            self._place_details(unquote(path.rsplit("/", 1)[-1]))
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = _read_json(self)
        except (ValueError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "invalid_json"})
            return

        if path == "/__mock__/post-rejected":
            with _lock:
                _counts["task_post"] += 1
            _json_response(self, 200, {"status_code": 40100, "status_message": "Synthetic rejection", "tasks": []})
        elif path == "/v3/serp/google/maps/task_post":
            self._task_post(body)
        elif path.startswith("/v3/serp/google/maps/task_get/advanced/"):
            self._task_get(unquote(path.rsplit("/", 1)[-1]))
        elif path.startswith("/v1/places/"):
            self._place_details(unquote(path.rsplit("/", 1)[-1]))
        elif path == "/internal/events":
            self._crm_event(body)
        else:
            _json_response(self, 404, {"error": "not_found"})

    def _task_post(self, body: dict | list) -> None:
        expected = "Basic " + base64.b64encode(
            f"{os.environ['MOCK_DATAFORSEO_USER']}:{os.environ['MOCK_DATAFORSEO_PASSWORD']}".encode()
        ).decode()
        if self.headers.get("Authorization") != expected:
            _json_response(
                self,
                200,
                {"status_code": 40100, "status_message": "Authentication failed.", "tasks": []},
            )
            return
        if not isinstance(body, list) or len(body) != 9:
            _json_response(
                self,
                200,
                {"status_code": 40000, "status_message": "Expected nine task items.", "tasks": []},
            )
            return
        if any(
            task.get("priority") != 1
            or task.get("language_code") != "pt"
            or task.get("depth") != 20
            for task in body
        ):
            _json_response(
                self,
                200,
                {"status_code": 40000, "status_message": "Unexpected Standard Normal task options.", "tasks": []},
            )
            return

        response_tasks = []
        with _lock:
            _counts["task_post"] += 1
            for request in body:
                task_id = str(uuid.uuid4())
                _tasks[task_id] = {"request": request, "polls": 0}
                response_tasks.append(
                    {
                        "id": task_id,
                        "status_code": 20100,
                        "status_message": "Task Created.",
                        "cost": 0.0,
                        "data": request,
                    }
                )
        _json_response(
            self,
            200,
            {
                "version": "homolog",
                "status_code": 20000,
                "status_message": "Ok.",
                "cost": 0.0,
                "tasks_count": len(response_tasks),
                "tasks_error": 0,
                "tasks": response_tasks,
            },
        )

    def _task_get(self, task_id: str) -> None:
        expected = "Basic " + base64.b64encode(
            f"{os.environ['MOCK_DATAFORSEO_USER']}:{os.environ['MOCK_DATAFORSEO_PASSWORD']}".encode()
        ).decode()
        if self.headers.get("Authorization") != expected:
            _json_response(
                self,
                200,
                {"status_code": 40100, "status_message": "Authentication failed.", "tasks": []},
            )
            return
        with _lock:
            record = _tasks.get(task_id)
            _counts["task_get"] += 1
            if record:
                record["polls"] += 1
                task = dict(record["request"])
        if not record:
            _json_response(
                self,
                200,
                {"status_code": 40401, "status_message": "Task not found.", "tasks": []},
            )
            return

        keyword = task.get("keyword", "")
        if keyword == "homolog-sem-resultados":
            _json_response(
                self,
                200,
                {
                    "status_code": 40102,
                    "status_message": "No Search Results.",
                    "tasks": [{"id": task_id, "status_code": 40102, "status_message": "No Search Results."}],
                },
            )
            return

        coordinate = task.get("location_coordinate", "").split(",")
        try:
            lat, lng = float(coordinate[0]), float(coordinate[1])
        except (ValueError, IndexError):
            lat, lng = -23.55, -46.63
        items = [
            {
                "type": "maps_search",
                "rank_group": 1,
                "rank_absolute": 1,
                "title": "ZURI Homologação",
                "cid": "1234567890123456789",
                "place_id": "ChIJZuriHomolog",
                "latitude": lat,
                "longitude": lng,
                "rating": {"value": 4.8, "votes_count": 123},
                "category": "Serviços locais",
                "address": "São Paulo, SP",
            },
            {
                "type": "maps_search",
                "rank_group": 2,
                "rank_absolute": 2,
                "title": "Concorrente de Teste",
                "cid": "9876543210987654321",
                "place_id": "ChIJCompetitorMock",
                "latitude": lat + 0.001,
                "longitude": lng + 0.001,
                "rating": {"value": 4.5, "votes_count": 54},
                "category": "Serviços locais",
                "address": "São Paulo, SP",
            },
        ]
        _json_response(
            self,
            200,
            {
                "version": "homolog",
                "status_code": 20000,
                "status_message": "Ok.",
                "tasks": [
                    {
                        "id": task_id,
                        "status_code": 20000,
                        "status_message": "Ok.",
                        "result": [{"items": items}],
                    }
                ],
            },
        )

    def _place_details(self, place_id: str) -> None:
        expected_key = os.environ["MOCK_PLACES_API_KEY"]
        if self.headers.get("X-Goog-Api-Key") != expected_key:
            _json_response(self, 403, {"error": {"status": "PERMISSION_DENIED"}})
            return
        if not re.fullmatch(r"ChIJ[A-Za-z0-9_-]{4,80}", place_id):
            _json_response(self, 404, {"error": {"status": "NOT_FOUND"}})
            return
        with _lock:
            _counts["places"] += 1
        _json_response(
            self,
            200,
            {
                "id": place_id,
                "displayName": {"text": "ZURI Homologação"},
                "location": {"latitude": -23.55, "longitude": -46.63},
                "formattedAddress": "São Paulo, SP, Brasil",
                "primaryTypeDisplayName": {"text": "Serviços locais"},
            },
        )

    def _crm_event(self, body: dict | list) -> None:
        if self.headers.get("Authorization") != f"Bearer {os.environ['MOCK_CRM_TOKEN']}":
            _json_response(self, 401, {"error": "unauthorized"})
            return
        if not isinstance(body, dict):
            _json_response(self, 400, {"error": "invalid_event"})
            return
        event_key = body.get("event_key")
        if (
            not isinstance(event_key, str)
            or event_key != self.headers.get("X-Idempotency-Key")
            or not isinstance(body.get("event"), dict)
        ):
            _json_response(self, 422, {"error": "invalid_idempotency_contract"})
            return

        payload = json.dumps(body["event"], sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with sqlite3.connect(_crm_db, timeout=10) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crm_events (
                    event_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            row = connection.execute(
                "SELECT fingerprint, contact_id FROM crm_events WHERE event_key=?",
                (event_key,),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    _json_response(self, 409, {"error": "idempotency_conflict"})
                    return
                contact_id = row[1]
            else:
                event = body["event"]
                diagnostic = event.get("diagnostic", {})
                contact_id = f"mock-contact-{diagnostic.get('request_id', uuid.uuid4())}"
                connection.execute(
                    "INSERT INTO crm_events(event_key,fingerprint,contact_id) VALUES(?,?,?)",
                    (event_key, fingerprint, contact_id),
                )
        with _lock:
            _counts["crm_events"] += 1
        _json_response(self, 200, {"contact_id": contact_id, "idempotent": True})


def main() -> None:
    Path(_crm_db).parent.mkdir(parents=True, exist_ok=True)
    cert_path = os.environ.get("MOCK_TLS_CERT", "/tmp/mock-cert.pem")
    key_path = os.environ.get("MOCK_TLS_KEY", "/tmp/mock-key.pem")
    tls_server = ThreadingHTTPServer(("0.0.0.0", 443), MockHandler)
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    tls_server.socket = context.wrap_socket(tls_server.socket, server_side=True)
    threading.Thread(target=tls_server.serve_forever, daemon=True).start()
    http_server = ThreadingHTTPServer(("0.0.0.0", 8081), MockHandler)
    http_server.serve_forever()


if __name__ == "__main__":
    main()
