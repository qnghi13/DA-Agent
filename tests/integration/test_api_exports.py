"""Result export over HTTP: a job per export, indexes not SQL, files only for their workspace.

Jobs are completed here directly instead of by a worker. Needs Postgres
(DA_AGENT_TEST_POSTGRES_URL).
See docs/design/2026-11-14-result-export.md.
"""

import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord, result_to_json
from da_agent.api.app import ApiSettings, create_app
from da_agent.storage.exports import CHANGED_DATASET, export_file
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.workspaces import create_workspace
from tests.integration.conftest import POSTGRES_URL
from tests.integration.test_api import audit_actions, auth, ready_dataset

pytestmark = pytest.mark.integration
CSV = "﻿total\n100\n250\n"


@pytest.fixture
def exports(tmp_path: Path) -> Path:
    return tmp_path / "exports"


@pytest.fixture
def client(schema: str, tmp_path: Path, exports: Path) -> TestClient:
    assert POSTGRES_URL is not None
    settings = ApiSettings(
        database_url=POSTGRES_URL,
        schema=schema,
        uploads_root=tmp_path / "uploads",
        exports_root=exports,
    )
    return TestClient(create_app(settings))


@pytest.fixture
def key(conn: psycopg.Connection, workspace: UUID) -> str:
    return create_key(conn, workspace)


def finished_run(client: TestClient, conn: psycopg.Connection, key: str) -> str:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Totals?"}
    ).json()["id"]
    queries = [
        QueryRecord(sql="SELECT 1 AS n", columns=["n"], rows=[[1]]),
        QueryRecord(
            sql="SELECT total FROM orders", columns=["total"], rows=[[100]], truncated=True
        ),
    ]
    result = AgentResult(answer=AgentAnswer("number", 350), steps=[], queries=queries)
    _complete(conn, lambda job: result_to_json(result) if job.kind == "run" else {})
    return run_id


def _complete(conn: psycopg.Connection, result: Any) -> None:
    queue = JobQueue(conn)
    while (job := queue.claim("w", lease_seconds=30)) is not None:
        queue.complete(job.id, "w", result(job))


def start(client: TestClient, key: str, run_id: str, index: int = 1) -> Any:
    return client.post(f"/v1/runs/{run_id}/queries/{index}/export", headers=auth(key))


def finished_export(
    client: TestClient, conn: psycopg.Connection, key: str, exports: Path
) -> tuple[str, str]:
    run_id = finished_run(client, conn, key)
    export_id = start(client, key, run_id).json()["id"]
    exports.mkdir(exist_ok=True)
    export_file(exports, UUID(export_id)).write_text(CSV, encoding="utf-8")
    filename = f"da-agent-{run_id[:8]}-query-2.csv"
    _complete(conn, lambda job: {"rows": 2, "bytes": len(CSV.encode()), "filename": filename})
    return run_id, export_id


def test_an_export_of_a_finished_run_is_queued_and_logged(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id = finished_run(client, conn, key)

    response = start(client, key, run_id)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    job = JobQueue(conn).get(UUID(body["id"]), workspace)
    assert job is not None and job.kind == "export"
    assert job.payload["run_id"] == run_id and job.payload["query_index"] == 1
    assert "sql" not in job.payload
    assert "export.create" in audit_actions(conn)
    shown = client.get(f"/v1/exports/{body['id']}", headers=auth(key)).json()
    assert shown == {"id": body["id"], "status": "queued", "rows": None, "bytes": None,
                     "filename": None, "error": None}  # fmt: skip


@pytest.mark.security
def test_the_request_cannot_carry_sql(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id = finished_run(client, conn, key)

    response = client.post(
        f"/v1/runs/{run_id}/queries/1/export", headers=auth(key), json={"sql": "SELECT secret"}
    )

    job = JobQueue(conn).get(UUID(response.json()["id"]), workspace)
    assert job is not None and "sql" not in job.payload
    assert "SELECT secret" not in str(job.payload)


@pytest.mark.parametrize("index", ["2", "-1", "x"])
def test_an_unknown_index_is_404(
    client: TestClient, conn: psycopg.Connection, key: str, index: str
) -> None:
    run_id = finished_run(client, conn, key)

    response = client.post(f"/v1/runs/{run_id}/queries/{index}/export", headers=auth(key))

    assert response.status_code in (404, 422)


def test_an_unfinished_run_is_409(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Totals?"}
    ).json()["id"]

    assert start(client, key, run_id, 0).status_code == 409


def test_a_changed_dataset_is_409(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    run_id = finished_run(client, conn, key)
    conn.execute("UPDATE datasets SET updated_at = now() + interval '1 second'")

    response = start(client, key, run_id)

    assert response.status_code == 409
    assert response.json()["detail"] == CHANGED_DATASET


def test_a_finished_export_downloads_as_csv(
    client: TestClient, conn: psycopg.Connection, key: str, exports: Path
) -> None:
    run_id, export_id = finished_export(client, conn, key, exports)

    shown = client.get(f"/v1/exports/{export_id}", headers=auth(key)).json()
    response = client.get(f"/v1/exports/{export_id}/file", headers=auth(key))

    assert shown["status"] == "succeeded" and shown["rows"] == 2
    assert shown["filename"] == f"da-agent-{run_id[:8]}-query-2.csv"
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="da-agent-{run_id[:8]}-query-2.csv"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == CSV.encode()
    assert "export.download" in audit_actions(conn)


def test_a_failed_export_shows_its_reason(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id = finished_run(client, conn, key)
    export_id = start(client, key, run_id).json()["id"]
    queue = JobQueue(conn)
    job = queue.claim("w", lease_seconds=30)
    assert job is not None
    queue.fail(job.id, "w", "the result has more than 1,000,000 rows", retry_in_seconds=None)

    shown = client.get(f"/v1/exports/{export_id}", headers=auth(key)).json()
    file = client.get(f"/v1/exports/{export_id}/file", headers=auth(key))

    assert shown["status"] == "failed"
    assert shown["error"] == "the result has more than 1,000,000 rows"
    assert file.status_code == 409


@pytest.mark.security
def test_another_workspace_gets_404_for_status_and_file(
    client: TestClient, conn: psycopg.Connection, key: str, exports: Path
) -> None:
    run_id, export_id = finished_export(client, conn, key, exports)
    other = create_key(conn, create_workspace(conn, "other"))

    assert client.get(f"/v1/exports/{export_id}", headers=auth(other)).status_code == 404
    assert client.get(f"/v1/exports/{export_id}/file", headers=auth(other)).status_code == 404
    assert start(client, other, run_id).status_code == 404


def test_a_run_id_is_not_an_export_id(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id = finished_run(client, conn, key)

    assert client.get(f"/v1/exports/{run_id}", headers=auth(key)).status_code == 404
    assert client.get(f"/v1/exports/{run_id}/file", headers=auth(key)).status_code == 404


@pytest.mark.parametrize("gone", ["expired", "deleted"])
def test_an_expired_export_is_404_not_500(
    client: TestClient, conn: psycopg.Connection, key: str, exports: Path, gone: str
) -> None:
    _, export_id = finished_export(client, conn, key, exports)
    path = export_file(exports, UUID(export_id))
    if gone == "expired":
        a_day_ago = time.time() - 25 * 3600
        os.utime(path, (a_day_ago, a_day_ago))
    else:
        path.unlink()

    response = client.get(f"/v1/exports/{export_id}/file", headers=auth(key))

    assert response.status_code == 404
    assert response.json()["detail"] == "the export expired; download it again"


def test_an_export_starts_when_the_model_budget_is_spent(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    # An export runs a stored query in the sandbox and calls no model, so it costs nothing.
    run_id = finished_run(client, conn, key)
    conn.execute("UPDATE workspaces SET daily_budget_usd = 0 WHERE id = %s", (workspace,))

    assert start(client, key, run_id).status_code == 202


def test_a_file_purged_after_the_expiry_check_is_404_not_500(
    client: TestClient,
    conn: psycopg.Connection,
    key: str,
    exports: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, export_id = finished_export(client, conn, key, exports)
    path = export_file(exports, UUID(export_id))

    def checked_then_purged(target: Path, *, now: float) -> bool:
        target.unlink()
        return False

    monkeypatch.setattr("da_agent.api.app.is_expired", checked_then_purged)
    response = client.get(f"/v1/exports/{export_id}/file", headers=auth(key))

    assert not path.exists()
    assert response.status_code == 404
    assert response.json()["detail"] == "the export expired; download it again"
