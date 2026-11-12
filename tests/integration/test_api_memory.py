"""Memory and conversations over HTTP (ADR 0030): people write and review notes, a run records the
notes it will read, and nothing crosses workspaces. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.api.app import ApiSettings, create_app
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.memory import MemoryStore
from da_agent.storage.workspaces import create_workspace
from tests.integration.conftest import POSTGRES_URL
from tests.integration.test_api import audit_actions, auth, ready_dataset

pytestmark = pytest.mark.integration


@pytest.fixture
def client(schema: str, tmp_path: Path) -> TestClient:
    assert POSTGRES_URL is not None
    settings = ApiSettings(
        database_url=POSTGRES_URL, schema=schema, uploads_root=tmp_path / "uploads"
    )
    return TestClient(create_app(settings))


@pytest.fixture
def key(conn: psycopg.Connection, workspace: UUID) -> str:
    return create_key(conn, workspace)


NOTE = {"kind": "correction", "text": "Revenue excludes cancelled orders."}


def test_a_user_writes_reads_edits_and_deletes_a_note(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    created = client.post(f"/v1/datasets/{dataset_id}/memory", headers=auth(key), json=NOTE)
    assert created.status_code == 201
    note_id = created.json()["id"]

    [entry] = client.get(f"/v1/datasets/{dataset_id}/memory", headers=auth(key)).json()["memory"]
    assert entry["status"] == "approved" and entry["text"] == NOTE["text"]

    edited = client.patch(
        f"/v1/memory/{note_id}", headers=auth(key), json={"text": "Revenue excludes returns."}
    )
    assert edited.status_code == 200
    new_id = edited.json()["id"]
    current = client.get(f"/v1/datasets/{dataset_id}/memory", headers=auth(key)).json()["memory"]
    assert [e["id"] for e in current if e["superseded_by"] is None] == [new_id]

    assert client.delete(f"/v1/memory/{new_id}", headers=auth(key)).status_code == 204
    actions = audit_actions(conn)
    assert {"memory.create", "memory.edit", "memory.delete"} <= set(actions)


def test_suggestions_are_reviewed_before_they_count(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    store = MemoryStore(conn)
    approved = store.suggest(workspace, UUID(dataset_id), "alias", "DT is revenue.", author="agent")
    rejected = store.suggest(workspace, UUID(dataset_id), "alias", "DT is debt.", author="agent")

    assert client.post(f"/v1/memory/{approved}/approve", headers=auth(key)).status_code == 200
    assert client.post(f"/v1/memory/{rejected}/reject", headers=auth(key)).status_code == 200
    assert client.post(f"/v1/memory/{rejected}/approve", headers=auth(key)).status_code == 409
    assert [e.text for e in store.approved(workspace, UUID(dataset_id))] == ["DT is revenue."]
    assert {"memory.approve", "memory.reject"} <= set(audit_actions(conn))


def test_invalid_notes_are_refused(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = ready_dataset(client, conn, key)
    url = f"/v1/datasets/{dataset_id}/memory"
    assert (
        client.post(url, headers=auth(key), json={"kind": "secret", "text": "x"}).status_code == 422
    )
    assert (
        client.post(url, headers=auth(key), json={"kind": "example", "text": "x"}).status_code
        == 422
    )


def test_a_run_records_the_notes_it_reads_and_its_conversation(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    note_id = client.post(f"/v1/datasets/{dataset_id}/memory", headers=auth(key), json=NOTE).json()[
        "id"
    ]
    chat = client.post("/v1/conversations", headers=auth(key), json={"dataset_id": dataset_id})
    assert chat.status_code == 201
    run = client.post(
        "/v1/runs",
        headers=auth(key),
        json={"dataset_id": dataset_id, "question": "Total?", "conversation_id": chat.json()["id"]},
    )
    assert run.status_code == 202
    job = JobQueue(conn).get(UUID(run.json()["id"]), workspace)
    assert job is not None
    assert job.payload["memory_ids"] == [note_id]
    assert job.payload["conversation_id"] == chat.json()["id"]


def test_a_conversation_belongs_to_one_dataset(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    first = ready_dataset(client, conn, key)
    second = ready_dataset(client, conn, key)
    chat = client.post("/v1/conversations", headers=auth(key), json={"dataset_id": first}).json()
    run = client.post(
        "/v1/runs",
        headers=auth(key),
        json={"dataset_id": second, "question": "Total?", "conversation_id": chat["id"]},
    )
    assert run.status_code == 404


@pytest.mark.security
def test_another_workspace_cannot_read_or_change_memory_or_join_a_conversation(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    note_id = client.post(f"/v1/datasets/{dataset_id}/memory", headers=auth(key), json=NOTE).json()[
        "id"
    ]
    chat = client.post("/v1/conversations", headers=auth(key), json={"dataset_id": dataset_id})
    intruder = auth(create_key(conn, create_workspace(conn, "intruder")))

    assert client.get(f"/v1/datasets/{dataset_id}/memory", headers=intruder).status_code == 404
    assert (
        client.post(f"/v1/datasets/{dataset_id}/memory", headers=intruder, json=NOTE).status_code
        == 404
    )
    assert (
        client.patch(f"/v1/memory/{note_id}", headers=intruder, json={"text": "x"}).status_code
        == 404
    )
    assert client.post(f"/v1/memory/{note_id}/approve", headers=intruder).status_code == 404
    assert client.delete(f"/v1/memory/{note_id}", headers=intruder).status_code == 404
    assert (
        client.post(
            "/v1/conversations", headers=intruder, json={"dataset_id": dataset_id}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/runs",
            headers=intruder,
            json={"dataset_id": dataset_id, "question": "q", "conversation_id": chat.json()["id"]},
        ).status_code
        == 404
    )


def test_runs_show_which_conversation_they_belong_to(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    chat = client.post("/v1/conversations", headers=auth(key), json={"dataset_id": dataset_id})
    chat_id = chat.json()["id"]
    alone = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "One?"}
    ).json()["id"]
    inside = client.post(
        "/v1/runs",
        headers=auth(key),
        json={"dataset_id": dataset_id, "question": "Two?", "conversation_id": chat_id},
    ).json()["id"]

    listed = client.get(f"/v1/runs?dataset_id={dataset_id}", headers=auth(key)).json()["runs"]
    assert {run["id"]: run["conversation_id"] for run in listed} == {alone: None, inside: chat_id}
    assert client.get(f"/v1/runs/{inside}", headers=auth(key)).json()["conversation_id"] == chat_id
