"""Outbound request and token limits per route (docs/design/2026-11-20-llm-resilience.md)."""

import threading

import pytest

from da_agent.llm.ratelimit import MemoryRateLimiter, RouteLimit, estimate_tokens, route_key
from da_agent.llm.types import LLMRequest, Message, ThrottledError, ToolSpec

KEY = route_key("groq", "m")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def limiter(**limit: int) -> tuple[MemoryRateLimiter, Clock]:
    clock = Clock()
    return MemoryRateLimiter({KEY: RouteLimit(**limit)}, clock=clock), clock


def test_the_route_key_names_provider_and_model() -> None:
    assert KEY == "groq:m"


def test_requests_beyond_the_minute_wait_their_turn() -> None:
    limits, _ = limiter(rpm=60)

    assert [limits.acquire(KEY, 0, max_wait=30) for _ in range(60)] == [0.0] * 60
    assert limits.acquire(KEY, 0, max_wait=30) == pytest.approx(1.0)
    assert limits.acquire(KEY, 0, max_wait=30) == pytest.approx(2.0)


def test_the_bucket_refills_with_time() -> None:
    limits, clock = limiter(rpm=60)
    for _ in range(60):
        limits.acquire(KEY, 0, max_wait=30)

    clock.now = 10.0

    assert [limits.acquire(KEY, 0, max_wait=30) for _ in range(10)] == [0.0] * 10
    assert limits.acquire(KEY, 0, max_wait=30) == pytest.approx(1.0)


def test_tokens_wait_for_the_deficit() -> None:
    limits, _ = limiter(tpm=6000)

    assert limits.acquire(KEY, 3000, max_wait=30) == 0.0
    assert limits.acquire(KEY, 4000, max_wait=30) == pytest.approx(10.0)


def test_a_wait_beyond_the_limit_raises_and_reserves_nothing() -> None:
    limits, _ = limiter(tpm=6000)
    limits.acquire(KEY, 6000, max_wait=30)

    with pytest.raises(ThrottledError):
        limits.acquire(KEY, 5000, max_wait=30)

    assert limits.acquire(KEY, 1000, max_wait=30) == pytest.approx(10.0)


def test_a_request_bigger_than_the_minute_is_capped() -> None:
    limits, _ = limiter(tpm=6000)

    assert limits.acquire(KEY, 50_000, max_wait=30) == 0.0
    assert limits.acquire(KEY, 600, max_wait=30) == pytest.approx(6.0)


def test_settle_returns_unused_tokens() -> None:
    limits, _ = limiter(tpm=6000)
    limits.acquire(KEY, 6000, max_wait=30)

    limits.settle(KEY, reserved=6000, used=3000)

    assert limits.acquire(KEY, 3000, max_wait=30) == 0.0


def test_a_route_without_limits_never_waits() -> None:
    limits, _ = limiter(rpm=1)

    assert [limits.acquire("vllm:x", 10**9, max_wait=0) for _ in range(5)] == [0.0] * 5
    limits.settle("vllm:x", reserved=10, used=5)


def test_threads_never_reserve_beyond_the_limit() -> None:
    limits, _ = limiter(rpm=60)
    waits: list[float] = []
    lock = threading.Lock()

    def take() -> None:
        wait = limits.acquire(KEY, 0, max_wait=30)
        with lock:
            waits.append(wait)

    threads = [threading.Thread(target=take) for _ in range(90)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(wait == 0.0 for wait in waits) == 60
    assert sorted(waits)[-1] == pytest.approx(30.0)


def test_the_estimate_counts_messages_tools_and_the_output_cap() -> None:
    request = LLMRequest(
        messages=[Message(role="user", content="x" * 400)],
        tools=[ToolSpec(name="t", description="d", parameters={})],
        max_output_tokens=500,
    )

    estimate = estimate_tokens(request)

    assert 600 <= estimate <= 700
