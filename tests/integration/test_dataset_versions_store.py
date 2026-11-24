"""Dataset versions in Postgres: the backfill, one open version, the limit, apply, approve,
discard, and workspace scoping. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

from typing import Any
from uuid import UUID

import psycopg
import pytest

from da_agent.storage.dataset_versions import DatasetVersionStore
from da_agent.storage.datasets import DatasetNotFoundError, DatasetStateError, DatasetStore
from da_agent.storage.db import migrate
from da_agent.storage.workspaces import create_workspace

pytestmark = pytest.mark.integration

SPEC = {"tables": []}
FILES = [{"name": "a.csv", "stored_as": "a.csv", "bytes": 3, "added_in": 1}]
NEW = [{"name": "b.csv", "stored_as": "b.csv", "bytes": 3, "added_in": 2}]


def ready_dataset(conn: psycopg.Connection, workspace: UUID) -> UUID:
    """A ready dataset made by the normal upload path."""
    store = DatasetStore(conn)
    dataset_id = store.create(workspace, "d", "", files=FILES)
    store.mark_proposed(dataset_id, SPEC, {"tables": 1}, [])
    assert store.approve(workspace, dataset_id, None) == "ready"
    return dataset_id


def jobs(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE kind = 'version' ORDER BY created_at")
    return [r[0] for r in rows.fetchall()]


def test_backfill_gives_a_ready_dataset_version_one(conn: psycopg.Connection) -> None:
    workspace = create_workspace(conn, "old")
    ready = conn.execute(
        "INSERT INTO datasets (workspace_id, name, status, spec, summary, files) "
        "VALUES (%s, 'r', 'ready', '{\"x\": 1}', '{\"s\": 1}', '[{\"name\": \"a\"}]') RETURNING id",
        (workspace,),
    ).fetchone()
    pending = conn.execute(
        "INSERT INTO datasets (workspace_id, name, status) VALUES (%s, 'p', 'proposed') "
        "RETURNING id",
        (workspace,),
    ).fetchone()
    assert ready is not None and pending is not None
    # Roll the schema back to before 0017, then migrate again so the backfill sees these rows.
    conn.execute("DROP TABLE dataset_versions")
    conn.execute("ALTER TABLE datasets DROP COLUMN current_version")
    conn.execute("DELETE FROM schema_migrations WHERE name LIKE '0017%'")
    conn.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check")
    conn.execute(
        "ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check "
        "CHECK (kind IN ('ingest', 'build', 'run', 'export', 'query'))"
    )
    assert migrate(conn) == ["0017_dataset_versions.sql"]

    version = DatasetVersionStore(conn).current(ready[0])
    assert (version.version, version.status) == (1, "applied")
    assert version.upload_dir == version.build_dir == str(ready[0])
    assert version.spec == {"x": 1} and version.files == [{"name": "a"}]
    assert conn.execute("SELECT count(*) FROM dataset_versions").fetchone() == (1,)


def test_mark_ready_gives_version_one(conn: psycopg.Connection, workspace: UUID) -> None:
    store = DatasetStore(conn)
    dataset_id = store.create(workspace, "d", "", files=FILES)
    store.mark_proposed(dataset_id, SPEC, {}, [])
    store.mark_ready(dataset_id, {"tables": 2})
    version = DatasetVersionStore(conn).current(dataset_id)
    assert (version.version, version.summary, version.files) == (1, {"tables": 2}, FILES)
    store.mark_ready(dataset_id, {"tables": 3})  # a second call keeps the one row
    assert len(DatasetVersionStore(conn).list(workspace, dataset_id)) == 1


def test_approve_unchanged_gives_version_one(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = ready_dataset(conn, workspace)
    version = DatasetVersionStore(conn).current(dataset_id)
    assert (version.version, version.status, version.spec) == (1, "applied", SPEC)
    assert version.build_dir == str(dataset_id)


def test_open_version_numbers_stores_and_enqueues(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    assert store.open_version(workspace, dataset_id, NEW, f"{dataset_id}-v2", 20) == 2
    record = store.get(workspace, dataset_id, 2)
    assert record is not None
    assert (record.status, record.files, record.upload_dir) == ("uploaded", NEW, f"{dataset_id}-v2")
    assert jobs(conn) == [{"dataset_id": str(dataset_id), "version": 2, "step": "check"}]


def test_open_version_refuses_a_number_another_upload_took(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    # The caller wrote `added_in` with the number it expected; a different one would mean the
    # job finds no new file.
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    with pytest.raises(DatasetStateError, match="changed while you uploaded"):
        store.open_version(workspace, dataset_id, NEW, "v3", 20, version=3)
    assert [v.version for v in store.list(workspace, dataset_id)] == [1]
    assert jobs(conn) == []
    assert store.open_version(workspace, dataset_id, NEW, "v2", 20, version=2) == 2


def test_second_open_version_raises(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    store.open_version(workspace, dataset_id, NEW, "v2", 20)
    with pytest.raises(DatasetStateError, match="already open"):
        store.open_version(workspace, dataset_id, NEW, "v3", 20)
    assert len(store.list(workspace, dataset_id)) == 2


def test_unique_index_violation_becomes_a_state_error(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    """A second open row, past the check, is still refused by the index and translated."""
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    store.open_version(workspace, dataset_id, NEW, "v2", 20)
    conn.execute("UPDATE dataset_versions SET status = 'applied' WHERE version = 2")
    conn.execute(
        "INSERT INTO dataset_versions (dataset_id, version, upload_dir) VALUES (%s, 3, 'x')",
        (dataset_id,),
    )
    with pytest.raises(DatasetStateError, match="already open"):
        store.open_version(workspace, dataset_id, NEW, "v4", 20)


def test_the_limit_is_enforced(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    for _ in range(19):  # versions 2..20
        version = store.open_version(workspace, dataset_id, NEW, "v", 20)
        assert store.apply(dataset_id, version, f"v{version}", expected=("uploaded",))
    with pytest.raises(DatasetStateError, match="limit"):
        store.open_version(workspace, dataset_id, NEW, "v21", 20)


def test_the_limit_counts_only_applied_and_open_versions(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    # Failed and discarded versions keep no build, so they never use up the dataset's versions.
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    for status in ["failed", "discarded"] * 3:
        version = store.open_version(workspace, dataset_id, NEW, "v", 3)
        store.mark(dataset_id, version, status)
    assert store.open_version(workspace, dataset_id, NEW, "v8", 3) == 8  # 1, 8: two of three
    store.mark(dataset_id, 8, "applied")
    assert store.open_version(workspace, dataset_id, NEW, "v9", 3) == 9  # 1, 8, 9 (open)
    store.mark(dataset_id, 9, "failed")
    store.mark(dataset_id, store.open_version(workspace, dataset_id, NEW, "v10", 3), "applied")
    with pytest.raises(DatasetStateError, match="limit"):
        store.open_version(workspace, dataset_id, NEW, "v11", 3)


def test_open_version_refuses_when_the_data_changed_since_the_upload_began(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    # The upload linked version 1's files; version 2 was applied while it streamed.
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    applied = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.apply(dataset_id, applied, "v2", expected=("uploaded",))

    with pytest.raises(DatasetStateError, match="changed while you uploaded"):
        store.open_version(workspace, dataset_id, NEW, "v3", 20, version=3, base_version=1)

    assert [v.version for v in store.list(workspace, dataset_id)] == [1, 2]
    assert store.open_version(workspace, dataset_id, NEW, "v3", 20, base_version=2) == 3


def test_a_dataset_that_is_not_ready_raises(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = DatasetStore(conn).create(workspace, "d", "")
    with pytest.raises(DatasetStateError, match="only a ready one"):
        DatasetVersionStore(conn).open_version(workspace, dataset_id, NEW, "v2", 20)


def test_apply_copies_onto_the_dataset_and_is_idempotent(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, FILES + NEW, "v2", 20)
    new_spec = {"tables": [{"name": "t"}]}
    store.mark(
        dataset_id, version, "building",
        spec=new_spec, summary={"tables": 5}, documents=[{"name": "n"}], files=FILES + NEW,
    )  # fmt: skip
    store.apply(dataset_id, version, f"{dataset_id}-v2")
    row = conn.execute(
        "SELECT spec, summary, documents, files, current_version FROM datasets WHERE id = %s",
        (dataset_id,),
    ).fetchone()
    assert row == (new_spec, {"tables": 5}, [{"name": "n"}], FILES + NEW, 2)
    current = store.current(dataset_id)
    assert (current.version, current.status, current.build_dir) == (
        2,
        "applied",
        f"{dataset_id}-v2",
    )

    conn.execute("UPDATE datasets SET summary = '{\"later\": 1}' WHERE id = %s", (dataset_id,))
    store.apply(dataset_id, version, "other")  # already applied: nothing changes
    assert conn.execute("SELECT summary FROM datasets WHERE id = %s", (dataset_id,)).fetchone() == (
        {"later": 1},
    )
    assert store.current(dataset_id).build_dir == f"{dataset_id}-v2"


def test_mark_rejects_unknown_columns_and_statuses(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    with pytest.raises(ValueError):
        store.mark(dataset_id, 1, "applied", **{"status = 'x', error": "boom"})
    with pytest.raises(ValueError):
        store.mark(dataset_id, 1, "nonsense")


def test_mark_with_an_expected_status_changes_only_a_version_in_that_status(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, f"{dataset_id}-v2", 20)
    store.discard(workspace, dataset_id, version)

    assert not store.mark(dataset_id, version, "checking", expected=("uploaded", "checking"))
    record = store.get(workspace, dataset_id, version)
    assert record is not None and record.status == "discarded"

    other = store.open_version(workspace, dataset_id, NEW, f"{dataset_id}-v3", 20)
    assert store.mark(dataset_id, other, "checking", expected=("uploaded",), tables=[])
    record = store.get(workspace, dataset_id, other)
    assert record is not None and (record.status, record.tables) == ("checking", [])


@pytest.mark.parametrize("status", ["failed", "discarded", "needs_choice"])
def test_apply_refuses_a_version_not_in_a_worker_step(
    conn: psycopg.Connection, workspace: UUID, status: str
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, f"{dataset_id}-v2", 20)
    store.mark(dataset_id, version, status, spec={"tables": [{"name": "t"}]})

    assert not store.apply(dataset_id, version, f"{dataset_id}-v2", expected=("checking",))

    record = store.get(workspace, dataset_id, version)
    assert record is not None and record.status == status
    assert store.current(dataset_id).version == 1


def test_apply_from_the_owned_step_applies_and_a_repeat_is_a_no_op(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, f"{dataset_id}-v2", 20)
    store.mark(dataset_id, version, "checking")

    assert store.apply(dataset_id, version, f"{dataset_id}-v2", expected=("checking",))
    assert store.apply(dataset_id, version, f"{dataset_id}-v2", expected=("checking",))
    assert store.current(dataset_id).version == 2


def test_approve_stores_choices_and_enqueues_a_build(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.mark(dataset_id, version, "needs_choice")
    choices = {"tables": {"orders": "replace"}, "replace_files": {}, "apply_anyway": False}
    edits = {"tables": []}
    repairs = [{"kind": "x"}]
    assert store.approve(workspace, dataset_id, version, choices, edits, repairs) == "building"
    record = store.get(workspace, dataset_id, version)
    assert record is not None
    assert (record.status, record.choices, record.spec_edits, record.repairs) == (
        "building", choices, edits, repairs,
    )  # fmt: skip
    assert jobs(conn)[-1] == {"dataset_id": str(dataset_id), "version": version, "step": "build"}


@pytest.mark.parametrize("status", ["uploaded", "checking", "building", "applied", "failed"])
def test_approve_only_from_needs_choice_or_proposed(
    conn: psycopg.Connection, workspace: UUID, status: str
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.mark(dataset_id, version, status)
    with pytest.raises(DatasetStateError):
        store.approve(workspace, dataset_id, version, {}, None, [])


def test_discard_frees_the_slot(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.mark(dataset_id, version, "proposed")
    store.discard(workspace, dataset_id, version)
    record = store.get(workspace, dataset_id, version)
    assert record is not None and record.status == "discarded"
    assert store.open_version(workspace, dataset_id, NEW, "v3", 20) == 3


@pytest.mark.parametrize("status", ["checking", "building", "applied", "failed", "discarded"])
def test_discard_refuses_busy_and_finished_versions(
    conn: psycopg.Connection, workspace: UUID, status: str
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.mark(dataset_id, version, status)
    with pytest.raises(DatasetStateError):
        store.discard(workspace, dataset_id, version)


def _end_jobs(conn: psycopg.Connection, status: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = %s, finished_at = now() WHERE kind = 'version'", (status,)
    )


@pytest.mark.parametrize("ended", ["failed", "succeeded", "cancelled"])
@pytest.mark.parametrize("step", ["checking", "building"])
def test_discard_frees_a_version_whose_job_ended_in_its_step(
    conn: psycopg.Connection, workspace: UUID, step: str, ended: str
) -> None:
    # A worker killed on the last attempt, or a crash while marking: nothing will move it on.
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    store.mark(dataset_id, version, step)
    _end_jobs(conn, ended)

    store.discard(workspace, dataset_id, version)

    record = store.get(workspace, dataset_id, version)
    assert record is not None and record.status == "discarded"


def test_discard_waits_for_the_latest_job_of_the_version(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    _end_jobs(conn, "succeeded")  # the check ended
    store.mark(dataset_id, version, "proposed")
    store.approve(workspace, dataset_id, version, {}, None, [])  # the build job is queued

    with pytest.raises(DatasetStateError, match="still"):
        store.discard(workspace, dataset_id, version)


def test_another_workspace_never_sees_a_version(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "v2", 20)
    other = create_workspace(conn, "other")
    assert store.get(other, dataset_id, version) is None
    assert store.list(other, dataset_id) == []
    with pytest.raises(DatasetStateError):
        store.open_version(other, dataset_id, NEW, "v3", 20)
    with pytest.raises(DatasetStateError):
        store.approve(other, dataset_id, version, {}, None, [])
    with pytest.raises(DatasetStateError):
        store.discard(other, dataset_id, version)


def test_a_deleting_dataset_hides_and_refuses_its_versions(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset_id = ready_dataset(conn, workspace)
    store = DatasetVersionStore(conn)
    version = store.open_version(workspace, dataset_id, NEW, "up", 9)
    DatasetStore(conn).start_delete(workspace, dataset_id)

    assert store.get(workspace, dataset_id, 1) is None
    assert store.list(workspace, dataset_id) == []
    assert store.mark(dataset_id, version, "checking", expected=("uploaded",)) is False
    assert store.apply(dataset_id, version, "b", expected=("uploaded",)) is False
    assert store.current(dataset_id).version == 1
    with pytest.raises(DatasetNotFoundError):
        store.approve(workspace, dataset_id, version, {}, None, [])
    with pytest.raises(DatasetNotFoundError):
        store.discard(workspace, dataset_id, version)
    with pytest.raises(DatasetNotFoundError):
        store.open_version(workspace, dataset_id, NEW, "up2", 9)
