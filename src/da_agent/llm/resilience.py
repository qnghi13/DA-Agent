"""Breaker and outbound limit settings from the environment (ADR 0041).

DA_AGENT_ROUTE_LIMITS is JSON by route, such as
{"groq:openai/gpt-oss-20b": {"rpm": 30, "tpm": 8000}}; unset means no limits.
DA_AGENT_RATE_LIMITER is memory (one process) or postgres (shared by worker processes).
DA_AGENT_BREAKER_FAILURES and DA_AGENT_BREAKER_COOLDOWN_SECONDS tune the breaker.
Errors are ValueErrors that name the variable.
"""

import json
from collections.abc import Callable, Mapping
from typing import Literal

import psycopg

from da_agent.llm.breaker import BreakerPolicy
from da_agent.llm.ratelimit import MemoryRateLimiter, RateLimiter, RouteLimit

LimiterKind = Literal["memory", "postgres"]
LIMIT_FIELDS = {"rpm", "tpm"}


def route_limits_from_env(env: Mapping[str, str]) -> dict[str, RouteLimit]:
    name = "DA_AGENT_ROUTE_LIMITS"
    raw = env.get(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} is not valid JSON: {error.msg}") from error
    if not isinstance(data, dict):
        raise ValueError(
            f'{name} must map routes to limits, such as {{"groq:MODEL": {{"rpm": 30}}}}'
        )
    limits = {}
    for key, value in data.items():
        provider, _, model = key.partition(":")
        if not provider or not model:
            raise ValueError(f"{name}: route {key!r} must look like provider:model")
        if not isinstance(value, dict) or not value or set(value) - LIMIT_FIELDS:
            raise ValueError(f"{name}: limits of {key!r} must use rpm and tpm only")
        for field, number in value.items():
            if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                raise ValueError(f"{name}: {key} {field} must be a positive whole number")
        limits[key] = RouteLimit(**value)
    return limits


def limiter_kind_from_env(env: Mapping[str, str]) -> LimiterKind:
    name = "DA_AGENT_RATE_LIMITER"
    kind = env.get(name, "").strip() or "memory"
    if kind not in ("memory", "postgres"):
        raise ValueError(f"{name} must be memory or postgres, not {kind!r}")
    return kind  # type: ignore[return-value]


def _positive(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value <= 0:
        raise ValueError(f"{name} must be above zero")
    return value


def breaker_from_env(env: Mapping[str, str]) -> BreakerPolicy:
    default = BreakerPolicy()
    failures = _positive(env, "DA_AGENT_BREAKER_FAILURES", default.failures)
    if failures != int(failures):
        raise ValueError("DA_AGENT_BREAKER_FAILURES must be a whole number")
    cooldown = _positive(env, "DA_AGENT_BREAKER_COOLDOWN_SECONDS", default.cooldown_seconds)
    return BreakerPolicy(failures=int(failures), cooldown_seconds=cooldown)


def build_limiter(
    kind: LimiterKind,
    limits: Mapping[str, RouteLimit],
    connect: Callable[[], psycopg.Connection] | None = None,
) -> RateLimiter | None:
    """None without limits. The Postgres limiter needs a way to open its own connection."""
    if not limits:
        return None
    if kind == "postgres":
        if connect is None:
            raise ValueError("the postgres rate limiter needs a database")
        from da_agent.llm.pg_ratelimit import PostgresRateLimiter

        return PostgresRateLimiter(limits, connect)
    return MemoryRateLimiter(limits)
