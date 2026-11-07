"""Dataset lifecycle through jobs: ingest proposes a spec, the user approves or edits it, and only
a ready dataset of the asking workspace can be opened for a run.

Uses a fake Runner, so no Docker is needed. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

import json
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
from da_agent.ingestion.spec import DatasetSpec
from da_agent.runner.runner import BuildResult, BuildRun
from da_agent.storage.datasets import DatasetStateError, DatasetStore
from da_agent.storage.jobs import JobQueue
from da_agent.storage.sandbox_dataset import BUILD_REPORT
from da_agent.storage.workspaces import create_workspace
from da_agent.worker.datasets import BuildHandler, DatasetOpener, IngestHandler
from da_agent.worker.worker import PermanentJobError
from tests.integration.conftest import POSTGRES_URL
from tests.unit.test_prepare_pipeline import GOOD, FakeRunner, llm

pytestmark = pytest.mark.integration

SPEC = json.loads(GOOD)


def report(column: str = "total") -> BuildReport:
    built = ColumnBuild(name=column, source="Tổng", type="money", non_blank=2, parsed=2)
    table = TableBuild(
        name="orders", source="don", description="one order", rows=2, columns=[built]
    )
    return BuildReport(tables=[table])


class WritingRunner(FakeRunner):
    """A fake Runner whose builds write their report, as the sandbox does."""

    def build(self, request: BuildRun) -> BuildResult:
        result = super().build(request)
        assert result.output_dir is not None and result.report is not None
        (result.output_dir / BUILD_REPORT).write_text(result.report.model_dump_json())
        return result


def database(schema: str) -> dict[str, Any]:
    assert POSTGRES_URL is not None
    return {"url": POSTGRES_URL, "schema": schema}


def ingest(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path, runner: FakeRunner
) -> tuple[UUID, dict[str, Any]]:
    dataset_id = DatasetStore(conn).create(workspace, "Shop", "Revenue excludes cancelled orders.")
    job_id = JobQueue(conn).enqueue(workspace, "ingest", {"dataset_id": str(dataset_id)})
    job = JobQueue(conn).claim("w", lease_seconds=30)
    assert job is not None and job.id == job_id
    client, _ = llm([GOOD])
    handler = IngestHandler(
        **database(schema), runner=runner, llm=client, datasets_root=tmp_path / "datasets"
    )
    return dataset_id, handler(job, threading.Event())


def test_ingest_proposes_a_spec_and_installs_the_build(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    runner = WritingRunner(tmp_path, [report()])

    dataset_id, outcome = ingest(schema, conn, workspace, tmp_path, runner)

    record = DatasetStore(conn).get(dataset_id, workspace)
    assert record is not None and record.status == "proposed"
    assert record.spec == DatasetSpec.model_validate(SPEC).model_dump(mode="json")
    assert record.summary["tables"][0] == {
        "name": "orders",
        "rows": 2,
        "row": "one order",
        "columns": 1,
    }
    assert record.documents == [{"path": "README.md", "text": "notes"}]
    assert (tmp_path / "datasets" / str(dataset_id) / BUILD_REPORT).is_file()
    assert outcome["status"] == "ok"


def test_a_failed_ingest_marks_the_dataset_failed(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    runner = WritingRunner(tmp_path, [], ingest_ok=False)

    dataset_id, outcome = ingest(schema, conn, workspace, tmp_path, runner)

    record = DatasetStore(conn).get(dataset_id, workspace)
    assert record is not None and record.status == "failed"
    assert "could not be read" in (record.error or "")
    assert outcome["status"] == "ingest_failed"


def test_approving_the_proposed_spec_makes_the_dataset_ready_and_openable(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    dataset_id, _ = ingest(schema, conn, workspace, tmp_path, WritingRunner(tmp_path, [report()]))
    opener = DatasetOpener(**database(schema), runner=object(), datasets_root=tmp_path / "datasets")
    with pytest.raises(PermanentJobError, match="not ready"):
        opener(workspace, str(dataset_id))

    assert DatasetStore(conn).approve(workspace, dataset_id, None) == "ready"

    dataset, documents = opener(workspace, str(dataset_id))
    assert [table.name for table in dataset.describe()] == ["orders"]
    assert [d.name for d in documents] == ["README.md", "Description from the uploader"]
    assert documents[1].text == "Revenue excludes cancelled orders."
    assert JobQueue(conn).claim("w", lease_seconds=30) is None


def test_an_edited_spec_is_built_by_a_build_job(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    runner = WritingRunner(tmp_path, [report(), report("amount")])
    dataset_id, _ = ingest(schema, conn, workspace, tmp_path, runner)
    edited = json.loads(GOOD.replace('"total"', '"amount"'))

    assert DatasetStore(conn).approve(workspace, dataset_id, edited) == "building"
    job = JobQueue(conn).claim("w", lease_seconds=30)
    assert job is not None and job.kind == "build"
    BuildHandler(**database(schema), runner=runner, datasets_root=tmp_path / "datasets")(
        job, threading.Event()
    )

    record = DatasetStore(conn).get(dataset_id, workspace)
    assert record is not None and record.status == "ready"
    assert record.spec == DatasetSpec.model_validate(edited).model_dump(mode="json")
    opener = DatasetOpener(**database(schema), runner=object(), datasets_root=tmp_path / "datasets")
    dataset, _ = opener(workspace, str(dataset_id))
    assert dataset.describe()[0].columns[0].name == "amount"


def test_an_invalid_edited_spec_is_refused(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    dataset_id, _ = ingest(schema, conn, workspace, tmp_path, WritingRunner(tmp_path, [report()]))

    with pytest.raises(DatasetStateError, match="spec"):
        DatasetStore(conn).approve(workspace, dataset_id, {"tables": "not a list"})

    record = DatasetStore(conn).get(dataset_id, workspace)
    assert record is not None and record.status == "proposed"


def test_only_a_proposed_dataset_can_be_approved(conn: psycopg.Connection, workspace: UUID) -> None:
    dataset_id = DatasetStore(conn).create(workspace, "Shop", "")

    with pytest.raises(DatasetStateError, match="uploaded"):
        DatasetStore(conn).approve(workspace, dataset_id, None)


@pytest.mark.security
def test_another_workspace_cannot_read_approve_or_open_a_dataset(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    dataset_id, _ = ingest(schema, conn, workspace, tmp_path, WritingRunner(tmp_path, [report()]))
    DatasetStore(conn).approve(workspace, dataset_id, None)
    other = create_workspace(conn, "other")
    opener = DatasetOpener(**database(schema), runner=object(), datasets_root=tmp_path / "datasets")

    assert DatasetStore(conn).get(dataset_id, other) is None
    with pytest.raises(DatasetStateError, match="not found"):
        DatasetStore(conn).approve(other, dataset_id, None)
    with pytest.raises(PermanentJobError, match="not found"):
        opener(other, str(dataset_id))
    with pytest.raises(PermanentJobError, match="not found"):
        opener(workspace, "../" + str(dataset_id))
