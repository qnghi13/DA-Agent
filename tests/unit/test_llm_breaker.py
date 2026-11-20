"""Circuit breaker states (docs/design/2026-11-20-llm-resilience.md)."""

import threading

from da_agent.llm.breaker import BreakerPolicy, CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(failures: int = 5, cooldown: float = 30.0) -> tuple[CircuitBreaker, Clock]:
    clock = Clock()
    return CircuitBreaker(BreakerPolicy(failures, cooldown), clock=clock), clock


def test_it_opens_after_consecutive_failures_only() -> None:
    breaker, _ = make()
    for _ in range(4):
        breaker.record_failure()
    breaker.record_success()
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state == "closed"
    assert breaker.allow()

    breaker.record_failure()

    assert breaker.state == "open"
    assert not breaker.allow()


def test_after_the_cooldown_one_probe_goes_out() -> None:
    breaker, clock = make(failures=1)
    breaker.record_failure()
    clock.now = 29.9
    assert not breaker.allow()

    clock.now = 30.0
    assert breaker.allow()
    assert breaker.state == "half_open"
    assert not breaker.allow()


def test_a_successful_probe_closes_it() -> None:
    breaker, clock = make(failures=1)
    breaker.record_failure()
    clock.now = 30.0
    breaker.allow()

    breaker.record_success()

    assert breaker.state == "closed"
    assert breaker.allow()


def test_a_failed_probe_reopens_it_for_a_full_cooldown() -> None:
    breaker, clock = make(failures=3)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 30.0
    breaker.allow()

    breaker.record_failure()

    assert breaker.state == "open"
    clock.now = 59.9
    assert not breaker.allow()
    clock.now = 60.0
    assert breaker.allow()


def test_only_one_of_many_threads_gets_the_probe() -> None:
    breaker, clock = make(failures=1)
    breaker.record_failure()
    clock.now = 30.0
    start = threading.Barrier(8)
    results: list[bool] = []

    def probe() -> None:
        start.wait()
        results.append(breaker.allow())

    threads = [threading.Thread(target=probe) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
