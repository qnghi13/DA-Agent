"""Postgres connections and schema migrations.

Migrations are plain SQL files in `migrations/`, applied in name order, each in its own
transaction, and recorded in `schema_migrations`. An advisory lock lets several processes start at
once without applying a file twice. See docs/adr/0017.
"""

import re
from importlib import resources
from typing import Any
from uuid import UUID

import psycopg

# Any constant shared by every process that migrates this database.
MIGRATION_LOCK = 0x0DA_A6E7
_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def connect(url: str, *, schema: str | None = None) -> psycopg.Connection:
    """An autocommit connection. schema, for tests, sets the search path to one schema."""
    if schema is None:
        return psycopg.connect(url, autocommit=True)
    if not _SCHEMA_NAME.fullmatch(schema):
        raise ValueError(f"invalid schema name {schema!r}")
    return psycopg.connect(url, autocommit=True, options=f"-c search_path={schema}")


def returned_id(cursor: psycopg.Cursor[Any]) -> UUID:
    """The id from an INSERT ... RETURNING id."""
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("the insert returned no id")
    return row[0]


def _migration_files() -> list[tuple[str, str]]:
    folder = resources.files("da_agent.storage") / "migrations"
    files = [entry for entry in folder.iterdir() if entry.name.endswith(".sql")]
    return [
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in sorted(files, key=lambda e: e.name)
    ]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply the migrations this database lacks. Returns the names applied."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for name, sql in _migration_files():
            if name in done:
                continue
            conn.execute(sql)  # type: ignore[arg-type]  # trusted files shipped with the package
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied
