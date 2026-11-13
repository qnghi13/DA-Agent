"""Storage for result export: the job kind, the changed-dataset check, and file lifetimes.

The Postgres tests need DA_AGENT_TEST_POSTGRES_URL; the file tests do not.
"""

import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from da_agent.storage.datasets import DatasetStore
from da_agent.storage.exports import (
    dataset_changed_since_run,
    export_file,
    export_filename,
    is_expired,
    purge_expired,
)
from da_agent.storage.jobs import JobQueue

pytestmark = pytest.mark.integration


def _ready_dataset_and_run(conn: psycopg.Connection, workspace: UUID) -> tuple[UUID, UUID]:
    dataset = DatasetStore(conn).create(workspace, "shop", "")
    conn.execute(
        "UPDATE datasets SET status = 'ready', updated_at = now() - interval '1 hour' "
        "WHERE id = %s",
        (dataset,),
    )
    run = JobQueue(conn).enqueue(workspace, "run", {"dataset_id": str(dataset)})
    return dataset, run


def test_an_export_job_can_be_queued(conn: psycopg.Connection, workspace: UUID) -> None:
    job = JobQueue(conn).enqueue(workspace, "export", {"run_id": "r", "query_index": 0})

    found = JobQueue(conn).get(job, workspace)
    assert found is not None and found.kind == "export"


def test_a_dataset_unchanged_since_the_run_is_not_changed(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset, run = _ready_dataset_and_run(conn, workspace)

    assert dataset_changed_since_run(conn, workspace, run, dataset) is False


def test_a_dataset_rebuilt_after_the_run_counts_as_changed(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset, run = _ready_dataset_and_run(conn, workspace)
    conn.execute(
        "UPDATE datasets SET updated_at = now() + interval '1 second' WHERE id = %s", (dataset,)
    )

    assert dataset_changed_since_run(conn, workspace, run, dataset) is True


def test_a_dataset_that_is_not_ready_counts_as_changed(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset, run = _ready_dataset_and_run(conn, workspace)
    conn.execute(
        "UPDATE datasets SET status = 'building', updated_at = now() - interval '1 hour' "
        "WHERE id = %s",
        (dataset,),
    )

    assert dataset_changed_since_run(conn, workspace, run, dataset) is True


def test_a_missing_dataset_or_run_counts_as_changed(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    dataset, run = _ready_dataset_and_run(conn, workspace)

    assert dataset_changed_since_run(conn, workspace, run, uuid4()) is True
    assert dataset_changed_since_run(conn, workspace, uuid4(), dataset) is True


def test_old_exports_are_purged_and_new_ones_kept(tmp_path: Path) -> None:
    now = time.time()
    old, new = export_file(tmp_path, uuid4()), export_file(tmp_path, uuid4())
    for path, age_hours in ((old, 25), (new, 1)):
        path.write_text("n\n1\n", encoding="utf-8")
        os.utime(path, (now - age_hours * 3600, now - age_hours * 3600))

    assert purge_expired(tmp_path, now=now) == 1
    assert not old.exists() and new.exists()
    assert is_expired(old, now=now) is True
    assert is_expired(new, now=now) is False


def test_purging_a_missing_directory_is_a_no_op(tmp_path: Path) -> None:
    assert purge_expired(tmp_path / "missing", now=time.time()) == 0


def test_export_files_and_names() -> None:
    job = UUID("1234abcd-0000-0000-0000-000000000000")

    assert export_file(Path("/x"), job) == Path(f"/x/{job}.csv")
    assert export_filename(str(job), 0) == "da-agent-1234abcd-query-1.csv"
