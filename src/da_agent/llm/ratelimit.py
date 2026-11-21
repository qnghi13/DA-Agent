"""Outbound request and token limits per model route (ADR 0041).

Two token buckets per route: requests per minute and tokens per minute. Each holds one minute's
limit and refills continuously. A call reserves its cost at once, and a bucket may go below zero:
later callers queue behind it and wait for the deficit to refill. A call whose wait would pass
max_wait reserves nothing and raises ThrottledError, so the client can fall back. The token cost is
an estimate made before the call; settle() replaces it with the real usage afterwards.

MemoryRateLimiter is right for one process. PostgresRateLimiter (pg_ratelimit) shares the buckets
between processes that use the same provider key.
"""

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from da_agent.llm.types import LLMRequest, ThrottledError

CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class RouteLimit:
    rpm: int | None = None
    tpm: int | None = None


class RateLimiter(Protocol):
    def acquire(self, route_key: str, tokens: int, max_wait: float) -> float:
        """Reserve one request and these tokens; return the seconds to wait before the call.
        Raise ThrottledError when the wait would pass max_wait."""
        ...

    def settle(self, route_key: str, reserved: int, used: int) -> None:
        """Replace a reservation of tokens with what the call used."""
        ...

    def release(self, route_key: str, reserved: int) -> None:
        """Give back a reservation whose call never went out: the request and its tokens."""
        ...


def route_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def estimate_tokens(request: LLMRequest) -> int:
    """A rough size of the request before it is sent, plus the most it may write back."""
    text = json.dumps(
        [message.model_dump(mode="json") for message in request.messages]
        + [tool.model_dump(mode="json") for tool in request.tools],
        ensure_ascii=False,
    )
    return len(text) // CHARS_PER_TOKEN + request.max_output_tokens


def bucket_costs(limit: RouteLimit, tokens: int) -> list[tuple[str, float, float]]:
    """(kind, per-minute capacity, cost) for each bucket the limit sets. A cost above the
    capacity is capped, so a large request waits for a full bucket, never forever."""
    costs = []
    if limit.rpm:
        costs.append(("requests", float(limit.rpm), 1.0))
    if limit.tpm:
        costs.append(("tokens", float(limit.tpm), float(min(max(tokens, 0), limit.tpm))))
    return costs


class _Bucket:
    def __init__(self, capacity: float, now: float) -> None:
        self.capacity = capacity
        self.level = capacity
        self.updated = now

    def refill(self, now: float) -> None:
        rate = self.capacity / 60.0
        self.level = min(self.capacity, self.level + rate * (now - self.updated))
        self.updated = now

    def wait_for(self, cost: float) -> float:
        deficit = cost - self.level
        return max(0.0, deficit / (self.capacity / 60.0))


class MemoryRateLimiter:
    def __init__(
        self, limits: Mapping[str, RouteLimit], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limits = dict(limits)
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def _bucket(self, key: str, kind: str, capacity: float, now: float) -> _Bucket:
        bucket = self._buckets.get((key, kind))
        if bucket is None:
            bucket = self._buckets[(key, kind)] = _Bucket(capacity, now)
        bucket.refill(now)
        return bucket

    def acquire(self, route_key: str, tokens: int, max_wait: float) -> float:
        limit = self._limits.get(route_key)
        if limit is None:
            return 0.0
        with self._lock:
            now = self._clock()
            planned = [
                (self._bucket(route_key, kind, capacity, now), cost)
                for kind, capacity, cost in bucket_costs(limit, tokens)
            ]
            wait = max((bucket.wait_for(cost) for bucket, cost in planned), default=0.0)
            if wait > max_wait:
                raise ThrottledError(f"{route_key}: the outbound limit needs a {wait:.1f} s wait")
            for bucket, cost in planned:
                bucket.level -= cost
            return wait

    def settle(self, route_key: str, reserved: int, used: int) -> None:
        limit = self._limits.get(route_key)
        if limit is None or not limit.tpm:
            return
        with self._lock:
            bucket = self._buckets.get((route_key, "tokens"))
            if bucket is None:
                return
            reserved_cost = min(max(reserved, 0), limit.tpm)
            bucket.level = min(bucket.capacity, bucket.level + reserved_cost - max(used, 0))

    def release(self, route_key: str, reserved: int) -> None:
        limit = self._limits.get(route_key)
        if limit is None:
            return
        with self._lock:
            for kind, _, cost in bucket_costs(limit, reserved):
                bucket = self._buckets.get((route_key, kind))
                if bucket is not None:
                    bucket.level = min(bucket.capacity, bucket.level + cost)
