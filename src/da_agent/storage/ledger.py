"""A cost ledger in Postgres. The in-memory ledger forgets a run's spending when its job moves to
another worker, so a retried run would get its whole budget again; this one does not. It also
lets the API and the worker sum a workspace's spending for the day. See docs/adr/0022.
"""

import threading
from uuid import UUID

import psycopg

from da_agent.llm.client import CostLedger, CostRecord, CostTotals


def _job_id(run_id: str) -> UUID | None:
    try:
        return UUID(run_id)
    except ValueError:
        return None


class PostgresCostLedger(CostLedger):
    def __init__(self, conn: psycopg.Connection) -> None:
        super().__init__(None)
        self._conn = conn
        # LLMClient may be shared by threads; one connection serves one statement at a time.
        self._db_lock = threading.Lock()

    def record(self, record: CostRecord) -> None:
        with self._db_lock:
            self._conn.execute(
                "INSERT INTO llm_calls (run_id, workspace_id, provider, model, input_tokens, "
                "output_tokens, cached_input_tokens, cost_usd, latency_seconds) "
                "VALUES (%s, (SELECT workspace_id FROM jobs WHERE id = %s), %s, %s, %s, %s, %s, "
                "%s, %s)",
                (
                    record.run_id, _job_id(record.run_id), record.provider, record.model,
                    record.input_tokens, record.output_tokens, record.cached_input_tokens,
                    record.cost_usd, record.latency_seconds,
                ),
            )  # fmt: skip

    def totals(self, run_id: str | None = None) -> CostTotals:
        with self._db_lock:
            row = self._conn.execute(
                "SELECT count(*), coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0), "
                "coalesce(sum(cost_usd), 0) FROM llm_calls WHERE %s::text IS NULL OR run_id = %s",
                (run_id, run_id),
            ).fetchone()
        calls, input_tokens, output_tokens, cost = row or (0, 0, 0, 0.0)
        return CostTotals(
            calls=calls, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost
        )


def spent_today(conn: psycopg.Connection, workspace_id: UUID) -> float:
    """What the workspace's model calls cost since midnight UTC."""
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE workspace_id = %s "
        "AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
        (workspace_id,),
    ).fetchone()
    return float(row[0]) if row else 0.0


def daily_budget(conn: psycopg.Connection, workspace_id: UUID, default: float) -> float:
    row = conn.execute(
        "SELECT daily_budget_usd FROM workspaces WHERE id = %s", (workspace_id,)
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else default
