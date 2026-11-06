"""Run one query on a clean DuckDB dataset. Runs inside the sandbox; see docs/adr/0011.

The SQL guard in the Worker is the first layer. This is the second: the file opens read-only,
external access (files, URLs, extensions) is off, and the configuration is locked so a query cannot
turn it back on. The container around it is the third.
"""

import datetime as dt
import decimal
from pathlib import Path
from typing import Any

import duckdb

MAX_ERROR_CHARS = 500
ENGINE_CONFIG = {
    "access_mode": "READ_ONLY",
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "threads": 1,
    "memory_limit": "256MB",
    "lock_configuration": True,
}


def _cell(value: Any, max_chars: int) -> tuple[Any, bool]:
    if isinstance(value, bytes):
        return "<binary>", False
    if isinstance(value, decimal.Decimal):
        value = float(value)
    elif isinstance(value, (dt.date, dt.time, dt.timedelta, list, dict, tuple)):
        value = str(value)
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + "…", True
    return value, False


def run_query(
    database: Path, sql: str, *, max_rows: int = 50, max_cell_chars: int = 200
) -> dict[str, Any]:
    """Return {"columns", "rows", "truncated"}, or {"error"} with a message for the model."""
    try:
        with duckdb.connect(str(database), config=ENGINE_CONFIG) as connection:
            cursor = connection.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            fetched = cursor.fetchmany(max_rows + 1)
    except duckdb.Error as error:
        return {"error": str(error).strip()[:MAX_ERROR_CHARS]}
    truncated = len(fetched) > max_rows
    rows = []
    for raw in fetched[:max_rows]:
        row = []
        for value in raw:
            cell, cut = _cell(value, max_cell_chars)
            truncated = truncated or cut
            row.append(cell)
        rows.append(row)
    return {"columns": columns, "rows": rows, "truncated": truncated}
