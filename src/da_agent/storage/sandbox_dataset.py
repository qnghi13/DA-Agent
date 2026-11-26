"""The agent's Dataset over a clean DuckDB file, queried in the sandbox. See docs/adr/0011.

Two layers check a query before the container: the SQL guard here, which rejects anything but one
SELECT on the dataset's own tables, and the engine settings in the sandbox. The schema comes from
the build report, so describing the dataset starts no container.
"""

import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from da_agent.ingestion.build import BuildReport, ColumnBuild
from da_agent.runner.runner import DateSpan, SqlResult, SqlRun, StatsKind, StatsResult, StatsRun
from da_agent.security.sql_guard import SqlRejectedError, validate_select
from da_agent.storage.sqlite_dataset import (
    Column,
    QueryError,
    QueryErrorKind,
    QueryResult,
    TableInfo,
)

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


def _range(column: ColumnBuild) -> tuple[str, str] | None:
    if column.min_value is None or column.max_value is None:
        return None
    return column.min_value, column.max_value


class QueryRunner(Protocol):
    def query(self, request: SqlRun) -> SqlResult: ...
    def stats(self, request: StatsRun) -> StatsResult: ...


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
                    Column(
                        c.name,
                        SQL_TYPES.get(c.type, "VARCHAR"),
                        c.source,
                        tuple(c.values),
                        _range(c),
                        max(c.non_blank - c.parsed, 0),
                    )
                    for c in table.columns
                ],
                table.description,
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
            raise QueryError(str(error), "rejected") from error
        result = self._runner.query(
            SqlRun(dataset_id=self._dataset_id, sql=checked, max_rows=self._max_rows)
        )
        if result.status != "ok":
            kind: QueryErrorKind = (
                "timeout" if result.status == "timeout" else "limit" if result.status == "killed"
                else "engine"
            )  # fmt: skip
            raise QueryError(result.error or f"query failed: {result.status}", kind)
        return QueryResult(result.columns, result.rows, result.truncated)

    def stats(
        self,
        kind: StatsKind,
        sql: str,
        x: str,
        y: str,
        *,
        dimensions: Sequence[str] = (),
        before: DateSpan | None = None,
        after: DateSpan | None = None,
        periods: int = 1,
    ) -> StatsResult:
        """Run a fixed analysis (ADR 0051) on a guarded query, or raise QueryError. The dates the
        data covers come from the build report, so code can refuse a range that runs past them
        and leave out unfinished periods."""
        try:
            checked = validate_select(sql, dialect="duckdb")
        except SqlRejectedError as error:
            raise QueryError(str(error), "rejected") from error
        start, end = self._coverage(checked, x)
        result = self._runner.stats(
            StatsRun(
                dataset_id=self._dataset_id,
                analysis=kind,
                sql=checked,
                x=x,
                y=y,
                dimensions=list(dimensions),
                before=before,
                after=after,
                periods=periods,
                data_start=start,
                data_end=end,
            )
        )
        if result.status != "ok":
            kind_of: QueryErrorKind = (
                "timeout" if result.status == "timeout" else "limit" if result.status == "killed"
                else result.error_kind or "engine"
            )  # fmt: skip
            raise QueryError(result.error or f"analysis failed: {result.status}", kind_of)
        return result

    def _coverage(self, sql: str, x: str) -> tuple[dt.date | None, dt.date | None]:
        """The days the data behind x covers. First the date column x is made from, such as
        order_date in CAST(order_date AS DATE) AS day. Else the days every table the query reads
        covers: tables joined together cover only their overlap, so a calendar cannot stretch
        it. None when neither is known."""
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except ParseError:
            return None, None
        read = {table.name.lower() for table in tree.find_all(exp.Table)}
        tables = [table for table in self._tables if table.name.lower() in read]
        source = _source_column(tree, x)
        if source is not None:
            for table in tables:
                for column in table.columns:
                    found = column.name.lower() == source and column.type in ("DATE", "TIMESTAMP")
                    if found and column.value_range is not None:
                        return _day(column.value_range[0]), _day(column.value_range[1])
        spans = []
        for table in tables:
            days = [
                _day(bound)
                for column in table.columns
                if column.type in ("DATE", "TIMESTAMP") and column.value_range
                for bound in column.value_range
            ]
            if days:
                spans.append((min(days), max(days)))
        if not spans:
            return None, None
        return max(start for start, _ in spans), min(end for _, end in spans)


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text[:10])


def _source_column(tree: exp.Expr, x: str) -> str | None:
    """The first column inside the outer SELECT's expression named x, lower case."""
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return None
    for projection in select.expressions:
        if projection.alias_or_name == x:
            column = projection.find(exp.Column)
            return column.name.lower() if column is not None else None
    return None
