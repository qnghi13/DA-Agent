"""Outbound limits shared through Postgres by every process that uses a provider key (ADR 0041).

Each acquire is one transaction. It refills and locks the requests row, then the tokens row, always
in that order so two callers never deadlock. Refills use the database clock, so the clocks of the
machines do not matter. When the wait would pass max_wait, the transaction rolls back and nothing
is reserved. Any limiter error lets the call through (fail open) and is kept on the span: the
provider's own 429 remains the last limit, and a limiter outage must not stop the agent. A slow
database cannot stall calls either: the connection gives up within two seconds, and after a
failure the limiter waits retry_seconds before it connects again.
"""

import contextlib
import threading
import time
from collections.abc import Callable, Mapping

import psycopg
from opentelemetry import trace

from da_agent.llm.ratelimit import RouteLimit, bucket_costs
from da_agent.llm.types import ThrottledError
from da_agent.storage.db import search_path_option

TIMEOUT_SECONDS = 2

# Refill from the time since the last update, measured after the row lock is held, and lock it.
_REFILL = """
INSERT INTO rate_buckets AS b (route_key, kind, level, updated_at)
VALUES (%(key)s, %(kind)s, %(capacity)s, clock_timestamp())
ON CONFLICT (route_key, kind) DO UPDATE SET
    level = least(
        %(capacity)s,
        b.level + %(capacity)s / 60.0
            * greatest(0, extract(epoch FROM clock_timestamp() - b.updated_at))
    ),
    updated_at = clock_timestamp()
RETURNING level
"""
_RESERVE = "UPDATE rate_buckets SET level = level - %s WHERE route_key = %s AND kind = %s"
_RELEASE = (
    "UPDATE rate_buckets SET level = least(%s, level + %s) WHERE route_key = %s AND kind = %s"
)
_REFUND = (
    "UPDATE rate_buckets SET level = least(%s, level + %s) WHERE route_key = %s AND kind = 'tokens'"
)


def limiter_connection(url: str, *, schema: str | None = None) -> psycopg.Connection:
    """An autocommit connection that gives up quickly: connecting, waiting for a row lock, and
    running a statement each stop after TIMEOUT_SECONDS."""
    timeout_ms = TIMEOUT_SECONDS * 1000
    options = f"-c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}"
    path = search_path_option(schema)
    return psycopg.connect(
        url, autocommit=True, connect_timeout=TIMEOUT_SECONDS,
        options=f"{path} {options}" if path else options,
    )  # fmt: skip


class _Unavailable(Exception):
    """The limiter skips the database until its retry time."""


class PostgresRateLimiter:
    def __init__(
        self,
        limits: Mapping[str, RouteLimit],
        connect: Callable[[], psycopg.Connection],
        *,
        clock: Callable[[], float] = time.monotonic,
        retry_seconds: float = 10.0,
    ) -> None:
        self._limits = dict(limits)
        self._connect = connect
        self._clock = clock
        self._retry_seconds = retry_seconds
        self._retry_at = float("-inf")
        self._conn: psycopg.Connection | None = None
        # One connection per limiter; threads take turns, so transactions never interleave.
        self._lock = threading.Lock()

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            if self._clock() < self._retry_at:
                raise _Unavailable("the limiter database failed recently")
            self._conn = self._connect()
        return self._conn

    def _fail_open(self, error: Exception) -> None:
        span = trace.get_current_span()
        span.set_attribute("da_agent.rate_limiter_failed_open", True)
        if isinstance(error, _Unavailable):
            return
        span.record_exception(error)
        self._retry_at = self._clock() + self._retry_seconds
        if self._conn is not None:
            # Closing a broken connection may fail too.
            with contextlib.suppress(Exception):
                self._conn.close()
        self._conn = None

    def acquire(self, route_key: str, tokens: int, max_wait: float) -> float:
        limit = self._limits.get(route_key)
        if limit is None:
            return 0.0
        costs = bucket_costs(limit, tokens)
        with self._lock:
            try:
                conn = self._connection()
                # Raising inside the block rolls it back: nothing is reserved.
                with conn.transaction():
                    wait = 0.0
                    for kind, capacity, cost in costs:
                        row = conn.execute(
                            _REFILL, {"key": route_key, "kind": kind, "capacity": capacity}
                        ).fetchone()
                        level = float(row[0]) if row else capacity
                        wait = max(wait, (cost - level) / (capacity / 60.0))
                    if wait > max_wait:
                        raise ThrottledError(
                            f"{route_key}: the outbound limit needs a {wait:.1f} s wait"
                        )
                    for kind, _, cost in costs:
                        conn.execute(_RESERVE, (cost, route_key, kind))
                return max(0.0, wait)
            except ThrottledError:
                raise
            except Exception as error:  # Fail open on anything: the limiter must not stop calls.
                self._fail_open(error)
                return 0.0

    def release(self, route_key: str, reserved: int) -> None:
        limit = self._limits.get(route_key)
        if limit is None:
            return
        with self._lock:
            try:
                conn = self._connection()
                with conn.transaction():
                    for kind, capacity, cost in bucket_costs(limit, reserved):
                        conn.execute(_RELEASE, (capacity, cost, route_key, kind))
            except Exception as error:  # Fail open, as in acquire.
                self._fail_open(error)

    def settle(self, route_key: str, reserved: int, used: int) -> None:
        limit = self._limits.get(route_key)
        if limit is None or not limit.tpm:
            return
        refund = min(max(reserved, 0), limit.tpm) - max(used, 0)
        with self._lock:
            try:
                self._connection().execute(_REFUND, (float(limit.tpm), refund, route_key))
            except Exception as error:  # Fail open, as in acquire.
                self._fail_open(error)
