"""Progress events of runs. The worker appends them as the graph runs, and the API streams them in
id order, so a client that reconnects with the last id it saw misses nothing. See docs/adr/0021."""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

_TYPE = re.compile(r"[a-z_]{1,32}")


@dataclass(frozen=True)
class RunEvent:
    id: int
    type: str
    data: dict[str, Any]


class RunEvents:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def append(self, job_id: UUID, workspace_id: UUID, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        self._conn.execute(
            "INSERT INTO run_events (job_id, workspace_id, type, data) VALUES (%s, %s, %s, %s)",
            (job_id, workspace_id, kind if _TYPE.fullmatch(kind) else "event", Jsonb(event)),
        )

    def after(
        self, job_id: UUID, workspace_id: UUID, *, after_id: int, limit: int = 100
    ) -> list[RunEvent]:
        rows = self._conn.execute(
            "SELECT id, type, data FROM run_events WHERE job_id = %s AND workspace_id = %s "
            "AND id > %s ORDER BY id LIMIT %s",
            (job_id, workspace_id, after_id, limit),
        ).fetchall()
        return [RunEvent(id=row[0], type=row[1], data=row[2]) for row in rows]
