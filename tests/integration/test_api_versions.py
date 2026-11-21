"""Adding files over HTTP: a new version of a dataset, its review, approval, and discard.

Jobs are not run here; the version job has its own tests. Needs Postgres
(DA_AGENT_TEST_POSTGRES_URL).
"""

import dataclasses
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.api.app import ApiSettings, create_app
from da_agent.storage.dataset_versions import DatasetVersionStore
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.storage.workspaces import create_workspace
from tests.integration.conftest import POSTGRES_URL
from tests.integration.test_api import audit_actions, auth, ready_dataset
from tests.integration.test_limits import call

pytestmark = pytest.mark.integration

REPAIR = {"table": "orders", "path": "columns.Tổng.type", "before": "money", "after": "number"}
CHANGES = [
    {
        "table": "orders", "kind": "grown", "new_rows": 2,
        "allowed_choices": ["replace", "keep_both"], "overlaps": [{"kind": "range"}],
        "stale_metrics": [],
    },
    {"table": "items", "kind": "new", "new_rows": 3, "overlaps": [], "stale_metrics": []},
]  # fmt: skip


@pytest.fixture
def settings(schema: str, tmp_path: Path) -> ApiSettings:
    assert POSTGRES_URL is not None
    return ApiSettings(
        database_url=POSTGRES_URL,
        schema=schema,
        uploads_root=tmp_path / "uploads",
        max_files=3,
        max_file_bytes=1000,
        max_dataset_versions=3,
    )


@pytest.fixture
def client(settings: ApiSettings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def key(conn: psycopg.Connection, workspace: UUID) -> str:
    return create_key(conn, workspace)


def add(
    client: TestClient,
    key: str,
    dataset_id: str,
    files: list[tuple[str, bytes]],
    replaces: list[str] | None = None,
) -> Any:
    data = {"replaces": replaces} if replaces is not None else None
    return client.post(
        f"/v1/datasets/{dataset_id}/files",
        headers=auth(key),
        data=data,
        files=[("files", (name, body)) for name, body in files],
    )


def waiting(conn: psycopg.Connection, dataset_id: str, status: str = "needs_choice") -> None:
    """Put the open version (2) into a waiting state with a check result."""
    from psycopg.types.json import Jsonb

    conn.execute(
        "UPDATE dataset_versions SET status = %s, changes = %s, repairs = %s "
        "WHERE dataset_id = %s AND version = 2",
        (status, Jsonb(CHANGES), Jsonb([REPAIR]), dataset_id),
    )


def approve(client: TestClient, key: str, dataset_id: str, **body: Any) -> Any:
    return client.post(
        f"/v1/datasets/{dataset_id}/versions/2/approve",
        headers=auth(key),
        json={"choices": {"orders": "keep_both"}} | body,
    )


def kinds(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM jobs ORDER BY created_at")]


def test_new_files_open_a_version_with_kept_files_linked(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)

    response = add(client, key, dataset_id, [("Đơn mới.csv", b"id,total\n3,5\n")])

    assert response.status_code == 202
    assert response.json() == {"dataset_id": dataset_id, "version": 2, "status": "uploaded"}
    folder = settings.uploads_root / f"{dataset_id}-v2"
    assert sorted(p.name for p in folder.iterdir()) == ["Don_hang.csv", "Don_moi.csv", "README.md"]
    assert "version" in kinds(conn)
    listed = client.get(f"/v1/datasets/{dataset_id}/versions", headers=auth(key)).json()
    assert [(v["version"], v["status"]) for v in listed["versions"]] == [
        (1, "applied"),
        (2, "uploaded"),
    ]
    assert len(listed["versions"][1]["files"]) == 3
    assert {"changes", "repairs", "error", "created_at"} <= set(listed["versions"][1])
    assert "dataset.files_add" in audit_actions(conn)


def test_the_dataset_shows_its_current_and_open_version(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    before = client.get(f"/v1/datasets/{dataset_id}", headers=auth(key)).json()
    assert (before["current_version"], before["open_version"]) == (1, None)

    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    after = client.get(f"/v1/datasets/{dataset_id}", headers=auth(key)).json()

    assert after["current_version"] == 1
    assert after["open_version"]["version"] == 2
    assert after["open_version"]["status"] == "uploaded"


def test_a_dataset_that_is_not_ready_takes_no_files(client: TestClient, key: str) -> None:
    dataset_id = client.post(
        "/v1/datasets",
        headers=auth(key),
        data={"name": "x"},
        files=[("files", ("a.csv", b"a\n1\n"))],
    ).json()["id"]

    assert add(client, key, dataset_id, [("b.csv", b"a\n1\n")]).status_code == 409


def test_a_second_open_version_is_refused_and_leaves_no_folder(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])

    assert add(client, key, dataset_id, [("c.csv", b"a\n1\n")]).status_code == 409

    assert not (settings.uploads_root / f"{dataset_id}-v3").exists()
    assert (settings.uploads_root / f"{dataset_id}-v2").exists()


def test_the_version_limit_returns_409(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    store = DatasetVersionStore(conn)
    for _ in range(2):
        number = store.open_version(
            _workspace(conn, dataset_id), UUID(dataset_id), [], f"{dataset_id}-vx", 9
        )
        store.apply(UUID(dataset_id), number, f"{dataset_id}-v{number}", expected=("uploaded",))

    response = add(client, key, dataset_id, [("b.csv", b"a\n1\n")])

    assert response.status_code == 409
    assert "3 versions" in response.json()["detail"]


def _workspace(conn: psycopg.Connection, dataset_id: str) -> UUID:
    row = conn.execute("SELECT workspace_id FROM datasets WHERE id = %s", (dataset_id,)).fetchone()
    assert row is not None
    return row[0]


def test_the_file_limit_counts_kept_and_new_files(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)  # two files, limit three

    over = add(client, key, dataset_id, [("b.csv", b"a\n1\n"), ("c.csv", b"a\n1\n")])
    replacing = add(
        client, key, dataset_id, [("b.csv", b"a\n1\n"), ("c.csv", b"a\n1\n")],
        replaces=["Don_hang.csv", ""],
    )  # fmt: skip

    assert over.status_code == 413
    assert replacing.status_code == 202
    assert not any(settings.uploads_root.glob(f"{dataset_id}-v3"))


def test_a_file_over_the_size_limit_is_refused_and_leaves_no_folder(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)

    response = add(client, key, dataset_id, [("b.csv", b"x" * 2000)])

    assert response.status_code == 413
    assert not (settings.uploads_root / f"{dataset_id}-v2").exists()
    assert (
        DatasetVersionStore(conn).list(_workspace(conn, dataset_id), UUID(dataset_id))[-1].version
        == 1
    )


def test_replacing_a_file_the_dataset_does_not_have_is_a_bad_request(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)

    response = add(client, key, dataset_id, [("b.csv", b"a\n1\n")], replaces=["ghost.csv"])

    assert response.status_code == 400
    assert not (settings.uploads_root / f"{dataset_id}-v2").exists()


def test_replaces_must_match_the_files_one_to_one(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)

    assert (
        add(client, key, dataset_id, [("b.csv", b"a\n1\n")], replaces=["", ""]).status_code == 400
    )


@pytest.mark.security
def test_a_traversal_name_is_stored_safely_and_never_collides(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)

    response = add(client, key, dataset_id, [("../../evil/Don hang.csv", b"a\n1\n")])

    assert response.status_code == 202
    folder = settings.uploads_root / f"{dataset_id}-v2"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Don_hang.csv",
        "Don_hang_2.csv",
        "README.md",
    ]
    assert not (settings.uploads_root.parent / "evil").exists()


@pytest.mark.security
def test_another_workspace_gets_404_on_every_route(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    waiting(conn, dataset_id)
    other = create_key(conn, create_workspace(conn, "other"))

    assert add(client, other, dataset_id, [("c.csv", b"a\n1\n")]).status_code == 404
    assert client.get(f"/v1/datasets/{dataset_id}/versions", headers=auth(other)).status_code == 404
    assert approve(client, other, dataset_id).status_code == 404
    discard = client.post(f"/v1/datasets/{dataset_id}/versions/2/discard", headers=auth(other))
    assert discard.status_code == 404
    assert client.get(f"/v1/datasets/{dataset_id}", headers=auth(other)).status_code == 404


def test_an_unknown_version_is_404(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = ready_dataset(client, conn, key)

    assert approve(client, key, dataset_id).status_code == 404
    gone = client.post(f"/v1/datasets/{dataset_id}/versions/9/discard", headers=auth(key))
    assert gone.status_code == 404


def test_approving_a_version_that_is_not_waiting_is_409(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])  # still uploaded

    assert approve(client, key, dataset_id).status_code == 409


def test_a_valid_approval_stores_the_decisions_and_enqueues_the_build(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    waiting(conn, dataset_id)
    edits = {"tables": [{"source": "items", "name": "items", "columns": []}]}

    response = approve(client, key, dataset_id, spec=edits, repairs=[REPAIR])

    assert response.status_code == 200
    assert response.json()["status"] == "building"
    version = DatasetVersionStore(conn).get(_workspace(conn, dataset_id), UUID(dataset_id), 2)
    assert version is not None
    assert version.choices == {
        "tables": {"orders": "keep_both"}, "replace_files": {}, "apply_anyway": False,
    }  # fmt: skip
    assert version.repairs == [REPAIR]
    assert version.spec_edits is not None
    assert "dataset.version_approve" in audit_actions(conn)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": {}},  # a table with overlaps needs a choice
        {"choices": {"orders": "drop_overlap"}},  # not allowed for a range overlap
        {"choices": {"orders": "keep_both", "ghost": "replace"}},  # not a reported table
        {"choices": {"orders": "keep_both"}, "replace_files": {"orders": "README.md"}},
        {"choices": {"orders": "replace"}, "replace_files": {"orders": "ghost.csv"}},
        {"choices": {"orders": "keep_both"}, "repairs": [{**REPAIR, "after": "text"}]},
        {"choices": {"orders": "keep_both"}, "repairs": [{**REPAIR, "path": "columns.Tổng.name"}]},
        {"choices": {"orders": "keep_both"},
         "spec": {"tables": [{"source": "don", "name": "orders"}]}},
        {"choices": {"orders": "keep_both"}, "spec": {"tables": [{"source": "x"}]}},
        {
            "choices": {"orders": "keep_both"},
            "spec": {"tables": [{"source": "i", "name": "items", "drop_overlap": [
                {"file": "b.csv", "against": ["a.csv"], "key": []}]}]},
        },
        {"choices": {"orders": "keep_both"}, "apply_anyway": "yes"},
    ],
)  # fmt: skip
def test_an_approval_with_invalid_contents_is_422_and_changes_nothing(
    client: TestClient, conn: psycopg.Connection, key: str, body: dict[str, Any]
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    waiting(conn, dataset_id)

    response = client.post(
        f"/v1/datasets/{dataset_id}/versions/2/approve", headers=auth(key), json=body
    )

    assert response.status_code == 422
    version = DatasetVersionStore(conn).get(_workspace(conn, dataset_id), UUID(dataset_id), 2)
    assert version is not None and version.status == "needs_choice"
    assert kinds(conn).count("version") == 1


def test_a_replace_choice_names_a_stored_file_of_the_current_version(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    waiting(conn, dataset_id)

    response = approve(
        client, key, dataset_id,
        choices={"orders": "replace"}, replace_files={"orders": "Don_hang.csv"},
    )  # fmt: skip

    assert response.status_code == 200


def test_discard_frees_the_slot_and_removes_only_the_versions_folder(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    waiting(conn, dataset_id)

    response = client.post(f"/v1/datasets/{dataset_id}/versions/2/discard", headers=auth(key))

    assert response.status_code == 204
    assert not (settings.uploads_root / f"{dataset_id}-v2").exists()
    assert (settings.uploads_root / dataset_id / "Don_hang.csv").exists()
    assert "dataset.version_discard" in audit_actions(conn)
    assert add(client, key, dataset_id, [("c.csv", b"a\n1\n")]).json()["version"] == 3
    again = client.post(f"/v1/datasets/{dataset_id}/versions/2/discard", headers=auth(key))
    assert again.status_code == 409


def test_a_version_that_is_checking_cannot_be_discarded(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    conn.execute("UPDATE dataset_versions SET status = 'checking' WHERE version = 2")

    response = client.post(f"/v1/datasets/{dataset_id}/versions/2/discard", headers=auth(key))

    assert response.status_code == 409
    assert (settings.uploads_root / f"{dataset_id}-v2").exists()


@pytest.mark.security
def test_discard_never_removes_the_first_or_the_current_versions_folder(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    add(client, key, dataset_id, [("b.csv", b"a\n1\n")])
    conn.execute(
        "UPDATE dataset_versions SET status = 'needs_choice', upload_dir = %s WHERE version = 2",
        (dataset_id,),
    )

    response = client.post(f"/v1/datasets/{dataset_id}/versions/2/discard", headers=auth(key))

    assert response.status_code == 204
    assert (settings.uploads_root / dataset_id / "Don_hang.csv").exists()


def test_adding_files_counts_against_the_job_rate(
    conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    limited = TestClient(create_app(dataclasses.replace(settings, jobs_per_minute=2)))
    dataset_id = ready_dataset(limited, conn, key)  # the upload is the first job

    first = add(limited, key, dataset_id, [("b.csv", b"a\n1\n")])
    second = add(limited, key, dataset_id, [("c.csv", b"a\n1\n")])

    assert (first.status_code, second.status_code) == (202, 429)
    assert int(second.headers["retry-after"]) > 0


def test_adding_files_stops_when_the_daily_budget_is_spent(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    job_id = JobQueue(conn).enqueue(_workspace(conn, dataset_id), "run", {})
    PostgresCostLedger(conn).record(call(str(job_id), settings.daily_budget_usd + 1))

    response = add(client, key, dataset_id, [("b.csv", b"a\n1\n")])

    assert response.status_code == 429
    assert "budget" in response.json()["detail"]
