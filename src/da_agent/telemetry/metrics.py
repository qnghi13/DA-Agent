"""Prometheus metrics for SLOs and alerts (ADR 0044).

Each process counts what it does, in its own registry, and serves it on its own port, outside the
web app; Prometheus scrapes it, and its rules turn counts into SLIs and burn-rate alerts. Labels
hold only fixed words, provider and model names, and route templates: never user text.

Recording a metric must never break the work being measured, so every method here swallows and
logs its own errors.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

logger = logging.getLogger(__name__)

RUN_SECONDS = (1, 2, 5, 10, 20, 30, 60, 120, 300)
WAIT_SECONDS = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
CALL_SECONDS = (0.5, 1, 2, 5, 10, 20, 30, 60)

# The ops panel's outcome groups (da_agent.storage.ops._OUTCOME), so a metric and the dashboard
# never disagree on what a run's outcome was.
_MODEL_ERRORS = {"llm_error", "invalid_tool_call", "no_tool_call"}
_UNVERIFIED = {"unverified", "unreadable_values"}
_OWN_NAME = {"budget_exceeded", "iteration_limit", "needs_clarification", "deadline_exceeded"}


def _safe[**P](method: Callable[P, None]) -> Callable[P, None]:
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            method(*args, **kwargs)
        except Exception:
            logger.exception("could not record a metric")

    return wrapper


def run_outcome(status: str, result: dict[str, Any] | None) -> str:
    """One run's outcome, grouped as the ops dashboard groups it."""
    if status in ("queued", "running"):
        return "in_progress"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    result = result or {}
    stop = result.get("stop_reason")
    if stop == "answered":
        answer = result.get("answer") or {}
        return "abstained" if answer.get("kind") == "unanswerable" else "answered"
    if stop in _UNVERIFIED:
        return "unverified"
    if stop in _MODEL_ERRORS:
        return "model_error"
    if stop in _OWN_NAME:
        return str(stop)
    return "other"


class WorkerMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self._runs = Counter(
            "da_agent_runs_total", "Finished runs by outcome.", ["outcome"], registry=registry
        )
        self._attempts = Counter(
            "da_agent_job_attempts_total",
            "Job attempts by kind and how they ended.",
            ["kind", "result"],
            registry=registry,
        )
        self._run_seconds = Histogram(
            "da_agent_run_duration_seconds",
            "Wall time of one run attempt.",
            buckets=RUN_SECONDS,
            registry=registry,
        )
        self._wait_seconds = Histogram(
            "da_agent_queue_wait_seconds",
            "Time from queueing to the first attempt's start.",
            buckets=WAIT_SECONDS,
            registry=registry,
        )

    @_safe
    def run_finished(self, outcome: str) -> None:
        self._runs.labels(outcome=outcome).inc()

    @_safe
    def attempt(self, kind: str, result: str) -> None:
        self._attempts.labels(kind=kind, result=result).inc()

    @_safe
    def run_duration(self, seconds: float) -> None:
        self._run_seconds.observe(seconds)

    @_safe
    def queue_wait(self, seconds: float) -> None:
        self._wait_seconds.observe(seconds)


class LLMMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self._calls = Counter(
            "da_agent_llm_calls_total",
            "Model calls by route and result (ok, or the error kind).",
            ["provider", "model", "result"],
            registry=registry,
        )
        self._seconds = Histogram(
            "da_agent_llm_call_duration_seconds",
            "Latency of successful model calls.",
            ["provider", "model"],
            buckets=CALL_SECONDS,
            registry=registry,
        )
        self._tokens = Counter(
            "da_agent_llm_tokens_total", "Tokens by direction.", ["direction"], registry=registry
        )
        self._cost = Counter("da_agent_llm_cost_usd", "Model spending in USD.", registry=registry)
        self._breaker = Gauge(
            "da_agent_breaker_open",
            "1 while a route's circuit breaker is open or half-open in this process.",
            ["route"],
            registry=registry,
        )

    @_safe
    def call(
        self,
        provider: str,
        model: str,
        result: str,
        seconds: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._calls.labels(provider=provider, model=model, result=result).inc()
        if seconds is not None:
            self._seconds.labels(provider=provider, model=model).observe(seconds)
        if input_tokens:
            self._tokens.labels(direction="input").inc(input_tokens)
        if output_tokens:
            self._tokens.labels(direction="output").inc(output_tokens)
        if cost_usd:
            self._cost.inc(cost_usd)

    @_safe
    def breaker(self, route: str, is_open: bool) -> None:
        self._breaker.labels(route=route).set(1 if is_open else 0)


def serve(port: int, registry: CollectorRegistry = REGISTRY) -> None:
    """Serve /metrics on its own port, apart from any web app (ADR 0044)."""
    start_http_server(port, registry=registry)


@functools.cache
def worker_metrics() -> WorkerMetrics:
    """This process's worker metrics, in the default registry; made once per process."""
    return WorkerMetrics()


@functools.cache
def llm_metrics() -> LLMMetrics:
    """This process's model call metrics, in the default registry; made once per process."""
    return LLMMetrics()


class ApiMetrics:
    """Requests by route template, method, and status, and their latency (ADR 0044)."""

    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self._requests = Counter(
            "da_agent_http_requests_total",
            "API requests by route template, method, and status.",
            ["route", "method", "status"],
            registry=registry,
        )
        self._seconds = Histogram(
            "da_agent_http_request_duration_seconds",
            "API request latency by route template.",
            ["route"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=registry,
        )

    @_safe
    def request(self, route: str, method: str, status: int, seconds: float) -> None:
        self._requests.labels(route=route, method=method, status=str(status)).inc()
        self._seconds.labels(route=route).observe(seconds)

    def install(self, app: Any) -> None:
        """Count every request of a FastAPI app. The route label is the matched path template,
        such as /v1/runs/{run_id}, so ids never become labels."""

        @app.middleware("http")
        async def count(request: Any, call_next: Any) -> Any:
            started = time.monotonic()
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                route = request.scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                self.request(template, request.method, status, time.monotonic() - started)


@functools.cache
def api_metrics() -> ApiMetrics:
    return ApiMetrics()
