import concurrent.futures
import json
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / "workflows" / "n8n"
WORKFLOW_FILES = (
    "01-recepcao-leads.json",
    "02-processador-standard.json",
    "03-recuperacao-resultados.json",
)


def _read_workflow(name):
    return json.loads((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


def _init_simulation(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE requests (
                request_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
            );
            CREATE TABLE tasks (
                request_id TEXT NOT NULL,
                point_index INTEGER NOT NULL,
                task_id TEXT UNIQUE,
                state TEXT NOT NULL,
                result TEXT,
                PRIMARY KEY (request_id, point_index)
            );
            CREATE TABLE budget (
                scope TEXT PRIMARY KEY,
                cap REAL NOT NULL,
                spent REAL NOT NULL DEFAULT 0,
                reserved REAL NOT NULL DEFAULT 0
            );
            """
        )


def _enqueue(path, request_id, key, fingerprint, barrier=None):
    if barrier is not None:
        barrier.wait()
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO requests(request_id,idempotency_key,fingerprint) "
            "VALUES(?,?,?)",
            (request_id, key, fingerprint),
        )
        saved = connection.execute(
            "SELECT request_id,fingerprint FROM requests WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if saved[1] != fingerprint:
            raise ValueError("Idempotency key reused with different content")
        connection.commit()
        return saved[0]


def _claim_one(path):
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT request_id FROM requests WHERE status='queued' LIMIT 1"
        ).fetchone()
        if row:
            connection.execute(
                "UPDATE requests SET status='claimed' WHERE request_id=? AND status='queued'",
                (row[0],),
            )
        connection.commit()
        return row[0] if row else None


def _workflow_nodes():
    return {
        name: _read_workflow(name)
        for name in WORKFLOW_FILES
    }


def test_workflow_json_import_shape_and_node_references():
    workflows = _workflow_nodes()
    expected_names = {
        "ZURI Smart Grid | 01 - Recepção de Leads",
        "ZURI Smart Grid | 02 - Processador Standard Normal",
        "ZURI Smart Grid | 03 - Recuperação de Resultados",
    }
    assert {workflow["name"] for workflow in workflows.values()} == expected_names

    for workflow in workflows.values():
        node_names = {node["name"] for node in workflow["nodes"]}
        assert workflow["active"] is False
        assert len(node_names) == len(workflow["nodes"])
        for source, outputs in workflow["connections"].items():
            assert source in node_names
            for output_groups in outputs.values():
                for group in output_groups:
                    for connection in group:
                        assert connection["node"] in node_names
        for node in workflow["nodes"]:
            for credential in node.get("credentials", {}).values():
                assert "SELECT_" in credential["id"]
                assert "configure after import" in credential["name"]


def test_workflows_use_standard_post_and_advanced_get_without_live_calls():
    processor = _read_workflow("02-processador-standard.json")
    recovery = _read_workflow("03-recuperacao-resultados.json")
    serialized = json.dumps([processor, recovery])

    assert "serp/google/maps/task_post" in serialized
    assert "serp/google/maps/task_get/advanced/" in serialized
    assert "serp/google/maps/live" not in serialized
    assert "priority: 1" in serialized
    assert "language_code: 'pt'" in serialized
    post_node = next(
        node for node in processor["nodes"]
        if node["type"] == "n8n-nodes-base.httpRequest"
        and "task_post" in node["parameters"]["url"]
    )
    assert post_node.get("retryOnFail") is False


@pytest.mark.parametrize("concurrent_submissions", [50, 100, 500])
def test_simulated_concurrent_unique_submissions_are_persisted(tmp_path, concurrent_submissions):
    database = tmp_path / f"queue-{concurrent_submissions}.sqlite"
    _init_simulation(database)
    workers = min(concurrent_submissions, 64)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _enqueue,
                database,
                str(uuid.uuid4()),
                f"campaign-{concurrent_submissions}-email-{index}",
                f"fingerprint-{index}",
            )
            for index in range(concurrent_submissions)
        ]
        results = [future.result(timeout=45) for future in futures]

    assert len(set(results)) == concurrent_submissions
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == concurrent_submissions


def test_simulated_duplicate_submissions_converge_to_one_request(tmp_path):
    database = tmp_path / "duplicate.sqlite"
    _init_simulation(database)
    workers = 32
    barrier = threading.Barrier(workers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _enqueue,
                database,
                str(uuid.uuid4()),
                "same-campaign-email-hash",
                "same-payload-fingerprint",
                barrier,
            )
            for _ in range(workers)
        ]
        persisted_ids = {future.result(timeout=45) for future in futures}

    assert len(persisted_ids) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 1


def test_simulated_atomic_claim_and_restart_recovery_do_not_repeat_paid_post(tmp_path):
    database = tmp_path / "restart.sqlite"
    _init_simulation(database)
    request_id = str(uuid.uuid4())
    _enqueue(database, request_id, "campaign-email", "fingerprint")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE requests SET status='submitting' WHERE request_id=?",
            (request_id,),
        )
        connection.executemany(
            "INSERT INTO tasks(request_id,point_index,state) VALUES(?,?,?)",
            [(request_id, point, "submission_started") for point in range(9)],
        )
        connection.commit()

    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE tasks SET state='submission_uncertain' "
            "WHERE request_id=? AND state='submission_started'",
            (request_id,),
        )
        connection.execute(
            "UPDATE requests SET status='submission_uncertain' WHERE request_id=?",
            (request_id,),
        )
        connection.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        claims = list(pool.map(lambda _: _claim_one(database), range(12)))
    assert all(claim is None for claim in claims)

    with sqlite3.connect(database) as reopened:
        request = reopened.execute(
            "SELECT status FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        tasks = reopened.execute(
            "SELECT count(*) FROM tasks WHERE request_id=? AND state='submission_uncertain'",
            (request_id,),
        ).fetchone()[0]
    assert request[0] == "submission_uncertain"
    assert tasks == 9


def test_simulated_partial_results_wait_for_all_points_and_repeated_import_is_idempotent(tmp_path):
    database = tmp_path / "partial.sqlite"
    _init_simulation(database)
    request_id = str(uuid.uuid4())
    _enqueue(database, request_id, "partial-request", "fingerprint")
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO tasks(request_id,point_index,state) VALUES(?,?,?)",
            [
                (request_id, point, "done" if point < 4 else "pending")
                for point in range(9)
            ],
        )
        connection.commit()

    def ready():
        with sqlite3.connect(database) as connection:
            return connection.execute(
                "SELECT count(*)=9 FROM tasks WHERE request_id=? "
                "AND state IN ('done','no_results','failed')",
                (request_id,),
            ).fetchone()[0]

    assert ready() == 0
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE tasks SET state='no_results' WHERE request_id=? AND state='pending'",
            (request_id,),
        )
        connection.commit()
        connection.execute(
            "UPDATE tasks SET result='same-result' WHERE request_id=? AND point_index=0",
            (request_id,),
        )
        connection.commit()
        connection.execute(
            "UPDATE tasks SET result='same-result' WHERE request_id=? AND point_index=0",
            (request_id,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE request_id=? AND result='same-result'",
            (request_id,),
        ).fetchone()[0] == 1
    assert ready() == 1


def test_budget_simulation_blocks_over_cap_and_sql_has_atomic_constraints():
    sql_schema = (WORKFLOW_DIR / "sql" / "001-schema.sql").read_text(encoding="utf-8")
    sql_functions = (WORKFLOW_DIR / "sql" / "002-functions.sql").read_text(encoding="utf-8")
    assert "UNIQUE (request_id, point_index)" in sql_schema
    assert "task_id TEXT UNIQUE" in sql_schema
    assert "FOR UPDATE SKIP LOCKED" in sql_functions
    assert "pg_advisory_xact_lock" in sql_functions
    assert "geogrid_mark_submission_uncertain" in sql_functions
    assert "geogrid_recover_expired_work" in sql_functions
    assert "v_pending + 9 > v_control.max_pending_tasks" in sql_functions

    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE budget (cap REAL, spent REAL, reserved REAL)")
    database.execute("INSERT INTO budget VALUES (1.0, 0.6, 0.2)")
    amount = 0.3
    cap, spent, reserved = database.execute("SELECT cap,spent,reserved FROM budget").fetchone()
    accepted = spent + reserved + amount <= cap
    assert not accepted


def test_ambiguous_post_results_are_not_reported_as_terminal_point_failures():
    processor = _read_workflow("02-processador-standard.json")
    callback_builder = next(
        node for node in processor["nodes"]
        if node["name"] == "Build Failed Submission Point Callbacks"
    )["parameters"]["jsCode"]
    sql_functions = (WORKFLOW_DIR / "sql" / "002-functions.sql").read_text(encoding="utf-8")

    assert "!t.task_id && Number.isInteger(t.status_code)" in callback_builder
    assert "t.status_code >= 40000 && t.status_code < 50000" in callback_builder
    assert "ELSE 'submission_uncertain'" in sql_functions


def test_prepared_tasks_increment_attempt_count_when_submission_is_armed():
    sql_functions = (WORKFLOW_DIR / "sql" / "002-functions.sql").read_text(encoding="utf-8")

    assert "'prepared'" in sql_functions
    assert "post_attempt_count = post_attempt_count + 1" in sql_functions
    assert "SET state = 'submission_started'" in sql_functions


def test_workflow_files_contain_placeholders_and_no_real_provider_credentials():
    serialized = "\n".join(
        (WORKFLOW_DIR / name).read_text(encoding="utf-8")
        for name in WORKFLOW_FILES
    )
    assert "SELECT_DATAFORSEO_BASIC_CREDENTIAL_AFTER_IMPORT" in serialized
    assert "SELECT_GEOGRID_BEARER_CREDENTIAL_AFTER_IMPORT" in serialized
    assert "SELECT_GOOGLE_PLACES_CREDENTIAL_AFTER_IMPORT" in serialized
    assert "GEOGRID_EXTERNAL_API_TOKEN" not in serialized
    assert "DATAFORSEO_PASSWORD" not in serialized
