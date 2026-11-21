"""Outbound limits shared through Postgres (ADR 0041).

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

import psycopg
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from da_agent.llm.pg_ratelimit import PostgresRateLimiter
from da_agent.llm.ratelimit import RouteLimit
from da_agent.llm.types import ThrottledError
from da_agent.storage.db import connect
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration

KEY = "groq:m"


def limiter(schema: str, **limit: int) -> PostgresRateLimiter:
    assert POSTGRES_URL is not None
    return PostgresRateLimiter(
        {KEY: RouteLimit(**limit)}, connect=lambda: connect(POSTGRES_URL, schema=schema)
    )


def test_two_processes_share_one_quota(conn: psycopg.Connection, schema: str) -> None:
    first, second = limiter(schema, rpm=10), limiter(schema, rpm=10)

    waits = [(first if i % 2 else second).acquire(KEY, 0, max_wait=30) for i in range(12)]

    assert all(wait < 0.5 for wait in waits[:10])
    assert waits[10] == pytest.approx(6.0, abs=0.5)
    assert waits[11] == pytest.approx(12.0, abs=0.5)


def test_a_refused_token_bucket_rolls_back_the_request(
    conn: psycopg.Connection, schema: str
) -> None:
    limits = limiter(schema, rpm=100, tpm=1000)
    limits.acquire(KEY, 1000, max_wait=30)

    with pytest.raises(ThrottledError):
        limits.acquire(KEY, 1000, max_wait=1)

    level = conn.execute(
        "SELECT level FROM rate_buckets WHERE route_key = %s AND kind = 'requests'", (KEY,)
    ).fetchone()
    assert level is not None and level[0] == pytest.approx(99.0, abs=0.5)


def test_settle_refunds_unused_tokens(conn: psycopg.Connection, schema: str) -> None:
    limits = limiter(schema, tpm=6000)
    limits.acquire(KEY, 6000, max_wait=30)

    limits.settle(KEY, reserved=6000, used=1000)

    assert limits.acquire(KEY, 5000, max_wait=30) < 0.5


def test_a_route_without_limits_never_touches_the_database(schema: str) -> None:
    def refuse() -> psycopg.Connection:
        raise AssertionError("no connection expected")

    limits = PostgresRateLimiter({KEY: RouteLimit(rpm=1)}, connect=refuse)

    assert limits.acquire("vllm:x", 100, max_wait=0) == 0.0


def test_a_database_outage_lets_calls_through_and_marks_the_span() -> None:
    def down() -> psycopg.Connection:
        raise psycopg.OperationalError("connection refused")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    limits = PostgresRateLimiter({KEY: RouteLimit(rpm=1)}, connect=down)

    with provider.get_tracer("t").start_as_current_span("llm.complete"):
        waits = [limits.acquire(KEY, 10, max_wait=30) for _ in range(3)]
        limits.settle(KEY, reserved=10, used=5)

    assert waits == [0.0, 0.0, 0.0]
    (span,) = exporter.get_finished_spans()
    assert any(event.name == "exception" for event in span.events)
    assert span.attributes["da_agent.rate_limiter_failed_open"] is True


def test_concurrent_callers_never_reserve_beyond_the_quota(
    conn: psycopg.Connection, schema: str
) -> None:
    import threading

    limiters = [limiter(schema, rpm=10), limiter(schema, rpm=10)]
    waits: list[float] = []
    lock = threading.Lock()

    def take(index: int) -> None:
        wait = limiters[index % 2].acquire(KEY, 0, max_wait=120)
        with lock:
            waits.append(wait)

    threads = [threading.Thread(target=take, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(wait < 0.5 for wait in waits) == 10
    assert max(waits) == pytest.approx(36.0, abs=1.0)


def test_any_limiter_error_lets_calls_through() -> None:
    def broken() -> psycopg.Connection:
        raise RuntimeError("bad driver")

    limits = PostgresRateLimiter({KEY: RouteLimit(rpm=1)}, connect=broken)

    assert limits.acquire(KEY, 10, max_wait=30) == 0.0
    limits.settle(KEY, reserved=10, used=5)


def test_after_a_failure_the_limiter_waits_before_reconnecting() -> None:
    attempts: list[float] = []
    now = [0.0]

    def down() -> psycopg.Connection:
        attempts.append(now[0])
        raise psycopg.OperationalError("connection refused")

    limits = PostgresRateLimiter(
        {KEY: RouteLimit(rpm=1)}, connect=down, clock=lambda: now[0], retry_seconds=10.0
    )
    limits.acquire(KEY, 10, max_wait=30)
    now[0] = 9.9
    limits.acquire(KEY, 10, max_wait=30)
    now[0] = 10.0
    limits.acquire(KEY, 10, max_wait=30)

    assert attempts == [0.0, 10.0]


def test_the_limiter_connection_gives_up_quickly(schema: str) -> None:
    from da_agent.llm.pg_ratelimit import limiter_connection

    assert POSTGRES_URL is not None
    with limiter_connection(POSTGRES_URL, schema=schema) as conn:
        settings = {
            name: conn.execute(f"SHOW {name}").fetchone()[0]  # type: ignore[index]
            for name in ("statement_timeout", "lock_timeout", "search_path")
        }

    assert settings == {"statement_timeout": "2s", "lock_timeout": "2s", "search_path": schema}


def test_release_gives_back_the_request_and_its_tokens(
    conn: psycopg.Connection, schema: str
) -> None:
    limits = limiter(schema, rpm=1, tpm=1000)
    limits.acquire(KEY, 1000, max_wait=30)

    limits.release(KEY, 1000)

    assert limits.acquire(KEY, 1000, max_wait=30) < 0.5
