"""The audit log: one row per change a workspace makes, and per refused key."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


def record(
    conn: psycopg.Connection,
    action: str,
    *,
    workspace_id: UUID | None = None,
    key_id: UUID | None = None,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (workspace_id, key_id, action, target, detail) "
        "VALUES (%s, %s, %s, %s, %s)",
        (workspace_id, key_id, action, target, Jsonb(detail or {})),
    )
