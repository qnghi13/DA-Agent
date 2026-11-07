"""Dataset jobs and the dataset opener used by run jobs.

- An ingest job runs the ingestion pipeline on an upload (an LLM proposes the spec, sandboxes parse
  and build), installs the build under datasets_root/<dataset id>, and marks the dataset proposed.
- A build job rebuilds an upload with the spec the user edited, without a model call.
- The opener gives a run job a ready dataset of its own workspace, and nothing else.

A failed ingest or build is an outcome for the user, not a job failure: the dataset is marked
failed with a readable reason and the job completes. See docs/adr/0019.
"""

import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

from da_agent.agent.loop_types import Document
from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.pipeline import (
    PreparedDataset,
    parse_problems,
    prepare_dataset,
    rebuild_dataset,
)
from da_agent.ingestion.spec import DatasetSpec
from da_agent.llm.client import LLMClient
from da_agent.runner.runner import Runner
from da_agent.storage.datasets import DatasetRecord, DatasetStore
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job
from da_agent.storage.sandbox_dataset import QueryRunner, SandboxDataset
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.worker import PermanentJobError

DESCRIPTION_DOCUMENT = "Description from the uploader"
FAILURES = {
    "ingest_failed": "The uploaded files could not be read.",
    "build_failed": "The data could not be built into tables.",
}


def _summary(prepared: PreparedDataset, report: BuildReport) -> dict[str, Any]:
    """What the user reviews before approving: tables, parse problems, and the pipeline's fixes."""
    return {
        "status": prepared.status,
        "tables": [
            {"name": t.name, "rows": t.rows, "row": t.description, "columns": len(t.columns)}
            for t in report.tables
            if t.error is None
        ],
        "problems": parse_problems(report),
        "proposal_problems": prepared.proposal.problems if prepared.proposal else [],
        "dropped_rules": prepared.dropped_rules,
        "deduplicated": prepared.deduplicated,
    }


def _install(build_dir: Path, target: Path) -> None:
    """Move a build into place, replacing an older build of the same dataset."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.with_name(target.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        target.rename(old)
    shutil.move(str(build_dir), target)
    shutil.rmtree(old, ignore_errors=True)


def _dataset_id(job: Job) -> UUID:
    try:
        return UUID(str(job.payload.get("dataset_id")))
    except ValueError as error:
        raise PermanentJobError("the job names no valid dataset") from error


class _DatasetJob:
    def __init__(
        self, *, url: str, schema: str | None = None, runner: Runner, datasets_root: Path
    ) -> None:
        self.url = url
        self.schema = schema
        self.runner = runner
        self.datasets_root = datasets_root

    def _record(self, store: DatasetStore, job: Job) -> DatasetRecord:
        record = store.get(_dataset_id(job), job.workspace_id)
        if record is None:
            raise PermanentJobError("dataset not found")
        return record

    def _finish(
        self, store: DatasetStore, record: DatasetRecord, prepared: PreparedDataset
    ) -> BuildReport | None:
        """Install the build and return its report, or mark the dataset failed and return None."""
        build = prepared.build
        if (
            build is None
            or build.status != "ok"
            or build.report is None
            or build.output_dir is None
        ):
            store.mark_failed(record.id, FAILURES.get(prepared.status, FAILURES["build_failed"]))
            return None
        _install(build.output_dir, self.datasets_root / str(record.id))
        return build.report


class IngestHandler(_DatasetJob):
    def __init__(
        self,
        *,
        url: str,
        schema: str | None = None,
        runner: Runner,
        llm: LLMClient,
        datasets_root: Path,
        budgets: WorkspaceBudgets | None = None,
    ) -> None:
        super().__init__(url=url, schema=schema, runner=runner, datasets_root=datasets_root)
        self.llm = llm
        # When set, the spec proposal may spend only what the workspace has left today.
        self.budgets = budgets

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        with connect(self.url, schema=self.schema) as conn:
            store = DatasetStore(conn)
            record = self._record(store, job)
            budget = self.budgets.for_job(job) if self.budgets is not None else None
            prepared = prepare_dataset(
                str(record.id), self.runner, self.llm, run_id=str(job.id),
                description=record.description, budget=budget,
            )  # fmt: skip
            report = self._finish(store, record, prepared)
            if report is not None:
                spec = (prepared.spec or DatasetSpec(tables=[])).model_dump(mode="json")
                documents = prepared.ingest.report.documents if prepared.ingest.report else []
                summary = _summary(prepared, report)
                store.mark_proposed(record.id, spec, summary, documents)
        return {"status": prepared.status}


class BuildHandler(_DatasetJob):
    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        with connect(self.url, schema=self.schema) as conn:
            store = DatasetStore(conn)
            record = self._record(store, job)
            if record.status != "building" or record.spec is None:
                raise PermanentJobError(f"the dataset is {record.status}, not waiting for a build")
            spec = DatasetSpec.model_validate(record.spec)
            prepared = rebuild_dataset(str(record.id), self.runner, spec)
            report = self._finish(store, record, prepared)
            if report is not None:
                store.mark_ready(record.id, _summary(prepared, report))
        return {"status": prepared.status}


class DatasetOpener:
    """Opens a ready dataset for a run job, only for the workspace that owns it."""

    def __init__(
        self, *, url: str, schema: str | None = None, runner: QueryRunner, datasets_root: Path
    ) -> None:
        self.url = url
        self.schema = schema
        self.runner = runner
        self.datasets_root = datasets_root

    def __call__(
        self, workspace_id: UUID, dataset_id: str
    ) -> tuple[SandboxDataset, list[Document]]:
        try:
            key = UUID(dataset_id)
        except ValueError as error:
            raise PermanentJobError("dataset not found") from error
        with connect(self.url, schema=self.schema) as conn:
            record = DatasetStore(conn).get(key, workspace_id)
        if record is None:
            raise PermanentJobError("dataset not found")
        if record.status != "ready":
            raise PermanentJobError(f"the dataset is not ready to be queried ({record.status})")
        dataset = SandboxDataset.open(self.runner, self.datasets_root, str(record.id))
        documents = [Document(d["path"], d["text"]) for d in record.documents]
        if record.description:
            documents.append(Document(DESCRIPTION_DOCUMENT, record.description))
        return dataset, documents
