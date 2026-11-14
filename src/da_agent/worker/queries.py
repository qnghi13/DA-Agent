"""Query jobs: run SQL a person edited from a run's query (docs/design/2026-11-14-edit-sql.md).

The API checked the SQL before queuing; it is checked again here, because the payload is data in
a database row. The sandbox and the locked DuckDB engine stay the boundary, as for agent SQL.
Failures reach the person as the guard's or DuckDB's message, never sandbox stderr.
"""

import threading
from typing import Any
from uuid import UUID

from da_agent.runner.client import UNAVAILABLE
from da_agent.runner.runner import SandboxRunner, SqlResult, SqlRun
from da_agent.security.sql_guard import SqlRejectedError, validate_select
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job, JobQueue
from da_agent.worker.worker import PermanentJobError, RetryableJobError

QUERY_ROWS = 50
QUERY_TIMEOUT_SECONDS = 15
_FAILURES = {
    "timeout": f"the query took longer than {QUERY_TIMEOUT_SECONDS} s; add a filter or aggregate",
    "killed": "the query ran out of memory; select fewer columns or aggregate",
}


def _id(value: Any) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as error:
        raise PermanentJobError("not found") from error


def check_sql(sql: Any) -> str:
    """The SQL guard, with its message kept for the person who wrote the query."""
    try:
        return validate_select(str(sql), dialect="duckdb")
    except SqlRejectedError as error:
        raise PermanentJobError(str(error)) from error


class QueryHandler:
    def __init__(self, *, url: str, schema: str | None = None, runner: SandboxRunner) -> None:
        self.url = url
        self.schema = schema
        self.runner = runner

    def _dataset(self, job: Job) -> UUID:
        run_id, dataset_id = _id(job.payload.get("run_id")), _id(job.payload.get("dataset_id"))
        with connect(self.url, schema=self.schema) as conn:
            run = JobQueue(conn).get(run_id, job.workspace_id)
            if run is None or run.kind != "run" or run.status != "succeeded":
                raise PermanentJobError("not found")
            if run.payload.get("dataset_id") != str(dataset_id):
                raise PermanentJobError("not found")
            row = conn.execute(
                "SELECT status FROM datasets WHERE id = %s AND workspace_id = %s",
                (dataset_id, job.workspace_id),
            ).fetchone()
        if row is None:
            raise PermanentJobError("not found")
        if row[0] != "ready":
            raise PermanentJobError("the dataset is not ready")
        return dataset_id

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        sql = check_sql(job.payload.get("sql"))
        dataset_id = self._dataset(job)
        result: SqlResult = self.runner.query(
            SqlRun(
                dataset_id=str(dataset_id), sql=sql, max_rows=QUERY_ROWS,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
            )
        )  # fmt: skip
        if result.status == "error" and result.error == UNAVAILABLE:
            raise RetryableJobError(UNAVAILABLE)
        if result.status in _FAILURES:
            raise PermanentJobError(_FAILURES[result.status])
        if result.status != "ok":
            # SqlResult.error holds DuckDB's message or a fixed one, never sandbox stderr.
            raise PermanentJobError(result.error or "the query failed")
        return {
            "sql": sql,
            "columns": result.columns,
            "rows": result.rows,
            "truncated": result.truncated,
        }
