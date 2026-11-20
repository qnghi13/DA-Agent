"""Cooperative stops: cancel and deadline (ADR 0042)."""

import pytest

from da_agent.agent.stop import RunStopped, StopToken


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_the_deadline_counts_from_the_token_and_stops_after_it() -> None:
    clock = Clock()
    token = StopToken(deadline_seconds=300, clock=clock)
    clock.now = 399.9
    token.check()
    assert token.reason is None

    clock.now = 400.0

    with pytest.raises(RunStopped) as caught:
        token.check()
    assert caught.value.reason == "deadline_exceeded"


def test_without_a_deadline_only_a_cancel_stops_it() -> None:
    clock = Clock()
    token = StopToken(clock=clock)
    clock.now = 10**9
    token.check()

    token.cancel("cancelled")

    with pytest.raises(RunStopped, match="cancelled"):
        token.check()


def test_the_first_reason_wins() -> None:
    clock = Clock()
    token = StopToken(deadline_seconds=1, clock=clock)
    token.cancel("cancelled")
    clock.now = 1000.0
    token.cancel("lease_lost")

    assert token.reason == "cancelled"


def test_a_probe_is_asked_at_most_once_per_interval() -> None:
    clock = Clock()
    asked: list[float] = []

    def probe() -> str | None:
        asked.append(clock.now)
        return "cancelled" if clock.now >= 102.0 else None

    token = StopToken(clock=clock, probe=probe, probe_every=1.0)
    token.check()
    clock.now = 100.5
    token.check()
    clock.now = 101.0
    token.check()
    clock.now = 102.0

    with pytest.raises(RunStopped, match="cancelled"):
        token.check()
    assert asked == [100.0, 101.0, 102.0]


def test_a_failing_probe_never_stops_the_run() -> None:
    def probe() -> str | None:
        raise RuntimeError("database down")

    token = StopToken(probe=probe, probe_every=0.0)

    token.check()
    assert token.reason is None
