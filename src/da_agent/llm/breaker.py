"""A circuit breaker per model route, kept in this process (ADR 0041).

Closed: calls go through, and consecutive network failures are counted. Open: calls are refused
until the cooldown passes. Half-open: one caller probes the route; its success closes the breaker,
its failure opens it for another cooldown. Each process watches its own calls, as Hystrix,
Resilience4j, and Envoy outlier detection do: sharing would add a dependency that can fail too.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

State = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class BreakerPolicy:
    failures: int = 5
    cooldown_seconds: float = 30.0


class CircuitBreaker:
    def __init__(self, policy: BreakerPolicy, clock: Callable[[], float] = time.monotonic) -> None:
        self._policy = policy
        self._clock = clock
        self._lock = threading.Lock()
        self._state: State = "closed"
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """Whether a call may go out now. After the cooldown, only the first caller probes."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if self._clock() - self._opened_at >= self._policy.cooldown_seconds:
                    self._state = "half_open"
                    return True
                return False
            return False  # A probe is already out.

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == "half_open" or self._failures >= self._policy.failures:
                self._state = "open"
                self._opened_at = self._clock()
