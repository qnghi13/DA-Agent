"""Metrics read from Postgres, for state that lives there (ADR 0044).

Queue depth, the oldest job waiting to start, each workspace's share of its daily model budget,
and ratings in the last 7 days. Run outcomes and latency are counted by the processes instead, so
those alerts still work when Postgres does not. Values are read at most every 30 seconds; when
Postgres cannot be read, the last values stay and da_agent_db_metrics_up drops to 0. A scrape never
waits on Postgres: while one read is in progress, others serve the last values, and the read
itself gives up after a few seconds (connect_db_metrics).
"""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30.0
TIMEOUT_SECONDS = 3

_QUEUE = "SELECT status, count(*) FROM jobs WHERE status IN ('queued', 'running') GROUP BY status"
# Only jobs that are due count: a job waiting out its retry delay is not stuck.
_OLDEST = (
    "SELECT coalesce(extract(epoch FROM now() - min(run_after)), 0)::float8 FROM jobs "
    "WHERE status = 'queued' AND run_after <= now()"
)
_BUDGET = """
    SELECT c.workspace_id, sum(c.cost_usd)::float8, w.daily_budget_usd
    FROM llm_calls c JOIN workspaces w ON w.id = c.workspace_id
    WHERE c.created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    GROUP BY c.workspace_id, w.daily_budget_usd
"""
_FEEDBACK = (
    "SELECT rating, count(*) FROM run_feedback "
    "WHERE updated_at >= now() - interval '7 days' GROUP BY rating"
)


def _ratio(spent: float, limit: float) -> float:
    """Share of the budget used. A zero budget (a frozen workspace) is over it once it spends."""
    if limit > 0:
        return spent / limit
    return float("inf") if spent > 0 else 0.0


def connect_db_metrics(url: str, schema: str | None = None) -> psycopg.Connection:
    """A connection that gives up quickly, so a stalled Postgres stalls no metrics read."""
    options = f"-c statement_timeout={TIMEOUT_SECONDS * 1000}"
    if schema is not None:
        options += f" -c search_path={schema}"
    return psycopg.connect(url, autocommit=True, connect_timeout=TIMEOUT_SECONDS, options=options)


class DatabaseCollector(Collector):
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        *,
        default_daily_usd: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._default_daily_usd = default_daily_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._read_at = float("-inf")
        self._values: dict[str, Any] | None = None
        self._up = False

    def _read(self) -> dict[str, Any]:
        with self._connect() as conn:
            queue = {"queued": 0, "running": 0} | dict(conn.execute(_QUEUE).fetchall())
            oldest_row = conn.execute(_OLDEST).fetchone()
            budget = {
                str(workspace): _ratio(
                    spent, limit if limit is not None else self._default_daily_usd
                )
                for workspace, spent, limit in conn.execute(_BUDGET).fetchall()
            }
            feedback = {"up": 0, "down": 0} | dict(conn.execute(_FEEDBACK).fetchall())
        return {
            "queue": queue,
            "oldest": float(oldest_row[0]) if oldest_row else 0.0,
            "budget": budget,
            "feedback": feedback,
        }

    def _current(self) -> dict[str, Any] | None:
        # Another scrape is reading: serve what we have rather than wait on Postgres.
        if not self._lock.acquire(blocking=False):
            return self._values
        try:
            now = self._clock()
            if now - self._read_at >= CACHE_SECONDS:
                self._read_at = now
                try:
                    self._values = self._read()
                    self._up = True
                except Exception as error:  # A scrape must still serve the process counters.
                    logger.warning("could not read metrics from Postgres: %s", error)
                    self._up = False
            return self._values
        finally:
            self._lock.release()

    def describe(self) -> list[GaugeMetricFamily]:
        # Registering must not read Postgres; the registry otherwise calls collect() to describe.
        return []

    def collect(self) -> Iterator[GaugeMetricFamily]:
        values = self._current()
        yield GaugeMetricFamily(
            "da_agent_db_metrics_up", "1 when the last read of Postgres metrics worked.",
            value=1 if self._up else 0,
        )  # fmt: skip
        if values is None:
            return
        queue = GaugeMetricFamily("da_agent_queue_jobs", "Jobs by status.", labels=["status"])
        for status, count in values["queue"].items():
            queue.add_metric([status], count)
        yield queue
        yield GaugeMetricFamily(
            "da_agent_queue_oldest_seconds", "Age of the oldest job that is due and waiting.",
            value=values["oldest"],
        )  # fmt: skip
        budget = GaugeMetricFamily(
            "da_agent_budget_used_ratio", "Share of today's model budget used, per workspace.",
            labels=["workspace_id"],
        )  # fmt: skip
        for workspace, ratio in values["budget"].items():
            budget.add_metric([workspace], ratio)
        yield budget
        feedback = GaugeMetricFamily(
            "da_agent_feedback_7d", "Ratings given in the last 7 days.", labels=["rating"]
        )
        for rating, count in values["feedback"].items():
            feedback.add_metric([rating], count)
        yield feedback
