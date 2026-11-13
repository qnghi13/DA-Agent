"""Export jobs: write one query of a finished run as a whole CSV file.

The job names a run and a query index, never SQL. The SQL comes from the stored run result and
passes the SQL guard again before a sandbox runs it. Failures reach the user as messages written
here or in the export report, never as sandbox stderr. See docs/design/2026-11-14-result-export.md.
"""

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from da_agent.runner.client import UNAVAILABLE
from da_agent.runner.runner import ExportRun, SandboxRunner
from da_agent.security.sql_guard import SqlRejectedError, validate_select
from da_agent.storage.csv_export import EXPORT_TIMEOUT_SECONDS
from da_agent.storage.db import connect
from da_agent.storage.exports import (
    CHANGED_DATASET,
    dataset_changed_since_run,
    export_file,
    export_filename,
    purge_expired,
)
from da_agent.storage.jobs import Job, JobQueue
from da_agent.worker.worker import PermanentJobError, RetryableJobError

_FAILURES = {
    "timeout": f"the export took longer than {EXPORT_TIMEOUT_SECONDS} s; add a filter or aggregate",
    "killed": "the export ran out of memory; select fewer columns or rows",
}


def _id(value: Any) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as error:
        raise PermanentJobError("not found") from error


class ExportHandler:
    def __init__(
        self, *, url: str, schema: str | None = None, runner: SandboxRunner, exports_root: Path
    ) -> None:
        self.url = url
        self.schema = schema
        self.runner = runner
        self.exports_root = exports_root

    def _stored_sql(self, job: Job) -> tuple[UUID, UUID, str, int]:
        run_id, dataset_id = _id(job.payload.get("run_id")), _id(job.payload.get("dataset_id"))
        index = job.payload.get("query_index")
        with connect(self.url, schema=self.schema) as conn:
            run = JobQueue(conn).get(run_id, job.workspace_id)
            if run is None or run.kind != "run" or run.status != "succeeded" or not run.result:
                raise PermanentJobError("not found")
            if run.payload.get("dataset_id") != str(dataset_id):
                raise PermanentJobError("not found")
            queries = run.result.get("queries") or []
            if not isinstance(index, int) or not 0 <= index < len(queries):
                raise PermanentJobError("not found")
            if dataset_changed_since_run(conn, job.workspace_id, run_id, dataset_id):
                raise PermanentJobError(CHANGED_DATASET)
        return run_id, dataset_id, str(queries[index]["sql"]), index

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        run_id, dataset_id, sql, index = self._stored_sql(job)
        try:
            checked = validate_select(sql, dialect="duckdb")
        except SqlRejectedError as error:
            raise PermanentJobError("this query cannot be exported") from error
        purge_expired(self.exports_root, now=time.time())
        result = self.runner.export(ExportRun(dataset_id=str(dataset_id), sql=checked))
        try:
            if result.status == "error" and result.stderr == UNAVAILABLE:
                raise RetryableJobError(UNAVAILABLE)
            if result.status != "ok" or result.report is None:
                raise PermanentJobError(_FAILURES.get(result.status, "the export failed"))
            if result.report.error:
                raise PermanentJobError(result.report.error)
            if result.output_dir is None:
                raise PermanentJobError("the export failed")
            self.exports_root.mkdir(parents=True, exist_ok=True)
            kept = export_file(self.exports_root, job.id)
            shutil.move(result.output_dir / "result.csv", kept)
        finally:
            if result.output_dir is not None:
                shutil.rmtree(result.output_dir, ignore_errors=True)
        return {
            "rows": result.report.rows,
            "bytes": kept.stat().st_size,
            "filename": export_filename(str(run_id), index),
        }
