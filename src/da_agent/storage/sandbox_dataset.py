"""The agent's Dataset over a clean DuckDB file, queried in the sandbox. See docs/adr/0011.

Two layers check a query before the container: the SQL guard here, which rejects anything but one
SELECT on the dataset's own tables, and the engine settings in the sandbox. The schema comes from
the build report, so describing the dataset starts no container.
"""

from pathlib import Path
from typing import Protocol

from da_agent.ingestion.build import BuildReport
from da_agent.runner.runner import SqlResult, SqlRun
from da_agent.security.sql_guard import SqlRejectedError, validate_select
from da_agent.storage.sqlite_dataset import Column, QueryError, QueryResult, TableInfo

BUILD_REPORT = "build_report.json"
SQL_TYPES = {
    "text": "VARCHAR",
    "integer": "BIGINT",
    "money": "BIGINT",
    "number": "DOUBLE",
    "quantity": "DOUBLE",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "boolean": "BOOLEAN",
}


class QueryRunner(Protocol):
    def query(self, request: SqlRun) -> SqlResult: ...


class SandboxDataset:
    dialect = "DuckDB"

    def __init__(
        self, runner: QueryRunner, dataset_id: str, report: BuildReport, *, max_rows: int = 50
    ) -> None:
        self._runner = runner
        self._dataset_id = dataset_id
        self._max_rows = max_rows
        self._tables = [
            TableInfo(
                table.name,
                table.rows,
                [
                    Column(c.name, SQL_TYPES.get(c.type, "VARCHAR"), c.source, tuple(c.values))
                    for c in table.columns
                ],
            )
            for table in report.tables
            if table.error is None
        ]

    @classmethod
    def open(cls, runner: QueryRunner, datasets_root: Path, dataset_id: str) -> "SandboxDataset":
        """Open a prepared dataset by its directory name under datasets_root."""
        path = datasets_root / dataset_id / BUILD_REPORT
        report = BuildReport.model_validate_json(path.read_text(encoding="utf-8"))
        return cls(runner, dataset_id, report)

    def describe(self) -> list[TableInfo]:
        return list(self._tables)

    def query(self, sql: str) -> QueryResult:
        """Run one read-only query in a fresh sandbox, or raise QueryError with a message."""
        try:
            checked = validate_select(sql, dialect="duckdb")
        except SqlRejectedError as error:
            raise QueryError(str(error)) from error
        result = self._runner.query(
            SqlRun(dataset_id=self._dataset_id, sql=checked, max_rows=self._max_rows)
        )
        if result.status != "ok":
            raise QueryError(result.error or f"query failed: {result.status}")
        return QueryResult(result.columns, result.rows, result.truncated)
