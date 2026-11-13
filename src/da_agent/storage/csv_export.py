"""Write a whole query result as CSV, or nothing (docs/design/2026-11-14-result-export.md).

Runs inside the sandbox, imported from /app, so it uses only DuckDB and the standard library. A
download that silently holds the first rows misleads, so any cap or error removes the file.
"""

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from da_agent.storage.duckdb_query import ENGINE_CONFIG

MAX_EXPORT_ROWS = 1_000_000
MAX_EXPORT_BYTES = 200 * 1024 * 1024
EXPORT_TIMEOUT_SECONDS = 120
EXPORT_TTL_SECONDS = 24 * 3600

_BATCH_ROWS = 10_000
# Text starting with these can run as a formula in a spreadsheet (OWASP, CSV injection).
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


class ExportReport(BaseModel):
    rows: int
    bytes: int
    columns: list[str]
    # Set when the export was refused: a cap or a query error, in words for the user.
    error: str | None = None


class ExportLimitError(ValueError):
    """The result is over a cap. The message tells the user what to change."""


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return f"'{value}" if value.startswith(_FORMULA_STARTS) else value
    if isinstance(value, dt.date | dt.datetime | dt.time):
        return value.isoformat()
    return value


class _CountingFile:
    """A text file that refuses to grow past max_bytes of UTF-8."""

    def __init__(self, file: Any, max_bytes: int) -> None:
        self._file = file
        self._max_bytes = max_bytes
        self.bytes = len("﻿".encode())

    def write(self, text: str) -> int:
        self.bytes += len(text.encode())
        if self.bytes > self._max_bytes:
            megabytes = self._max_bytes // 1_048_576
            raise ExportLimitError(
                f"the file is over {megabytes} MB; select fewer columns or rows"
                if megabytes
                else f"the file is over {self._max_bytes} bytes; select fewer columns or rows"
            )
        return self._file.write(text)


def write_csv(
    database: Path,
    sql: str,
    out: Path,
    *,
    max_rows: int = MAX_EXPORT_ROWS,
    max_bytes: int = MAX_EXPORT_BYTES,
) -> ExportReport:
    """Run sql on the database, read-only with external access off (ADR 0011), and write every
    row to out. Raise, and leave no file, on any cap or error."""
    try:
        with duckdb.connect(str(database), config=ENGINE_CONFIG) as connection:
            cursor = connection.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            rows = 0
            with out.open("w", encoding="utf-8-sig", newline="") as file:
                counted = _CountingFile(file, max_bytes)
                writer = csv.writer(counted)
                # Column names come from the model's SQL aliases, so they are escaped like text.
                writer.writerow([_cell(column) for column in columns])
                while batch := cursor.fetchmany(_BATCH_ROWS):
                    rows += len(batch)
                    if rows > max_rows:
                        raise ExportLimitError(
                            f"the result has more than {max_rows:,} rows; add a filter or aggregate"
                        )
                    writer.writerows([_cell(value) for value in row] for row in batch)
        return ExportReport(rows=rows, bytes=out.stat().st_size, columns=columns)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
