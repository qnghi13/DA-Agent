"""A read-only SQLite dataset with hidden columns and bounded query results.

Three layers protect it: the SQL guard, the file opened as read-only and immutable, and a SQLite
authorizer that also enforces hidden columns however a query reaches them. Queries run in this
process, so use it only for trusted evaluation data until the sandbox exists.
"""

import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from da_agent.security.sql_guard import FORBIDDEN_FUNCTIONS, SqlRejectedError, validate_select

PROGRESS_EVERY_OPERATIONS = 1000


class QueryError(ValueError):
    """A query failed. The message is safe to show to the model."""


@dataclass(frozen=True)
class ReferenceTable:
    columns: list[str]
    rows: list[list[Any]]


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    # The column's name in the uploaded file, which documents may use.
    source: str | None = None
    # Every distinct value, for a text column with few of them.
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableInfo:
    name: str
    row_count: int
    columns: list[Column]


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SqliteDataset:
    dialect = "SQLite"

    def __init__(
        self,
        path: Path,
        *,
        hidden_columns: Iterable[str] = frozenset(),
        reference_tables: Mapping[str, ReferenceTable] | None = None,
        timeout_seconds: float = 5.0,
        max_rows: int = 50,
        max_cell_chars: int = 200,
    ) -> None:
        """Open the file. hidden_columns holds names in the form 'table.column'."""
        if not path.is_file():
            raise QueryError(f"dataset file not found: {path}")
        self._hidden = {name.lower() for name in hidden_columns}
        self._timeout = timeout_seconds
        self._max_rows = max_rows
        self._max_cell_chars = max_cell_chars
        self._deadline = 0.0

        # immutable=1 stops SQLite from creating journal or WAL files next to the data.
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True)
        for name, table in (reference_tables or {}).items():
            self._load_reference_table(name, table)
        self._tables = self._read_schema()
        self._connection.set_authorizer(self._authorize)
        self._connection.set_progress_handler(self._past_deadline, PROGRESS_EVERY_OPERATIONS)

    def _load_reference_table(self, name: str, table: ReferenceTable) -> None:
        # TEMP tables live outside the data file, which stays untouched.
        columns = ", ".join(_quote(column) for column in table.columns)
        marks = ", ".join("?" for _ in table.columns)
        self._connection.execute(f"CREATE TEMP TABLE {_quote(name)} ({columns})")
        self._connection.executemany(
            f"INSERT INTO temp.{_quote(name)} VALUES ({marks})",  # noqa: S608 - quoted config names
            table.rows,
        )

    def _read_schema(self) -> list[TableInfo]:
        query = (
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "UNION ALL SELECT name FROM sqlite_temp_master WHERE type = 'table'"
        )
        tables = []
        for (name,) in self._connection.execute(query).fetchall():
            if name.startswith("sqlite_"):
                continue
            quoted = _quote(name)
            count = self._connection.execute(
                f"SELECT count(*) FROM {quoted}"  # noqa: S608 - name comes from the schema
            ).fetchone()[0]
            columns = [
                Column(row[1], row[2])
                for row in self._connection.execute(f"PRAGMA table_info({quoted})")
                if f"{name}.{row[1]}".lower() not in self._hidden
            ]
            tables.append(TableInfo(name, count, columns))
        return tables

    def _authorize(self, action: int, arg1: Any, arg2: Any, _db: Any, _trigger: Any) -> int:
        if action == sqlite3.SQLITE_READ:
            hidden = f"{arg1}.{arg2}".lower() in self._hidden
            return sqlite3.SQLITE_DENY if hidden else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            denied = str(arg2).lower() in FORBIDDEN_FUNCTIONS
            return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _past_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def describe(self) -> list[TableInfo]:
        """Return tables and visible columns."""
        return list(self._tables)

    def query(self, sql: str) -> QueryResult:
        """Run one read-only query and return a bounded result, or raise QueryError."""
        try:
            checked = validate_select(sql)
        except SqlRejectedError as error:
            raise QueryError(str(error)) from error
        return self._execute_unchecked(checked)

    def _execute_unchecked(self, sql: str) -> QueryResult:
        self._deadline = time.monotonic() + self._timeout
        try:
            cursor = self._connection.execute(sql)
            fetched = cursor.fetchmany(self._max_rows + 1)
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                raise QueryError(f"query stopped: over the {self._timeout}s time limit") from error
            if "not authorized" in str(error) or "prohibited" in str(error):
                raise QueryError(
                    f"{error}: the query uses something not allowed or a hidden column. "
                    "Name only the columns from describe_dataset instead of using *."
                ) from error
            raise QueryError(str(error)) from error
        except sqlite3.Error as error:
            raise QueryError(str(error)) from error

        columns = [column[0] for column in cursor.description or []]
        cursor.close()
        truncated = len(fetched) > self._max_rows
        rows: list[list[Any]] = []
        for raw in fetched[: self._max_rows]:
            row = []
            for cell in raw:
                value = "<binary>" if isinstance(cell, bytes) else cell
                if isinstance(value, str) and len(value) > self._max_cell_chars:
                    value = value[: self._max_cell_chars] + "…"
                    truncated = True
                row.append(value)
            rows.append(row)
        return QueryResult(columns, rows, truncated)
