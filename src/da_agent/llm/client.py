"""LLMClient: the only way nodes reach a model. Owns retries, fallback, budgets, and costs.

See docs/adr/0002-custom-llm-client.md.
"""

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, Tracer

from da_agent.llm.breaker import BreakerPolicy, CircuitBreaker
from da_agent.llm.ratelimit import RateLimiter, estimate_tokens, route_key
from da_agent.llm.types import (
    AllRoutesFailedError,
    BudgetExceededError,
    CallInterruptedError,
    InvalidToolCallError,
    LLMAdapter,
    LLMError,
    LLMRequest,
    LLMResponse,
    PermanentLLMError,
    RateLimitedError,
    ThrottledError,
    TransientLLMError,
    Usage,
)

if TYPE_CHECKING:
    from da_agent.telemetry.metrics import LLMMetrics

BASE_WAIT_SECONDS = 1.0
DEFAULT_BREAKER = BreakerPolicy()
# How often a wait asks whether its caller stopped.
WAIT_SLICE_SECONDS = 1.0


@dataclass(frozen=True)
class ModelPrice:
    """Price in USD per million tokens. The default of zero is for free tiers and local models.

    Cached input tokens are part of input_tokens. They cost cached_input_per_million when it is set,
    and the full input price otherwise.
    """

    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cached_input_per_million: float | None = None

    def cost(self, usage: Usage) -> float:
        cached_price = self.cached_input_per_million
        if cached_price is None:
            cached_price = self.input_per_million
        cached = min(usage.cached_input_tokens, usage.input_tokens)
        return (
            (usage.input_tokens - cached) * self.input_per_million
            + cached * cached_price
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000


@dataclass(frozen=True)
class Route:
    adapter: LLMAdapter
    model: str
    price: ModelPrice = ModelPrice()


@dataclass(frozen=True)
class RunBudget:
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class CostRecord:
    run_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    latency_seconds: float
    # Set on a failed attempt: rate_limited, transient, permanent, or invalid_tool_call. Failed
    # attempts cost nothing and are not counted as calls, but operators see them (ADR 0026).
    error: str | None = None


def error_kind(error: LLMError) -> str:
    if isinstance(error, ThrottledError):
        return "throttled"
    if isinstance(error, RateLimitedError):
        return "rate_limited"
    if isinstance(error, TransientLLMError):
        return "transient"
    if isinstance(error, InvalidToolCallError):
        return "invalid_tool_call"
    return "permanent"


@dataclass(frozen=True)
class CostTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CostLedger:
    """Keeps a record of every call, and appends to a JSONL file when given a path.

    Failed attempts are kept with their error kind; totals count only successful calls.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._records: list[CostRecord] = []
        self._lock = threading.Lock()

    def record(self, record: CostRecord) -> None:
        with self._lock:
            self._records.append(record)
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(asdict(record)) + "\n")

    def records(self) -> list[CostRecord]:
        with self._lock:
            return list(self._records)

    def totals(self, run_id: str | None = None) -> CostTotals:
        """Sum one run, or every run when run_id is None."""
        with self._lock:
            records = [
                r
                for r in self._records
                if r.error is None and (run_id is None or r.run_id == run_id)
            ]
        return CostTotals(
            calls=len(records),
            input_tokens=sum(r.input_tokens for r in records),
            output_tokens=sum(r.output_tokens for r in records),
            cost_usd=sum(r.cost_usd for r in records),
        )


def _stop_if_asked(interrupted: Callable[[], bool] | None) -> None:
    if interrupted is not None and interrupted():
        raise CallInterruptedError("the run stopped before the model was called")


class LLMClient:
    def __init__(
        self,
        routes: Sequence[Route],
        ledger: CostLedger,
        *,
        max_retries: int = 2,
        max_wait: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        tracer: Tracer | None = None,
        record_content: bool = False,
        breaker_policy: BreakerPolicy | None = DEFAULT_BREAKER,
        clock: Callable[[], float] = time.monotonic,
        limiter: RateLimiter | None = None,
        max_throttle_wait: float = 300.0,
        metrics: "LLMMetrics | None" = None,
    ) -> None:
        if not routes:
            raise ValueError("LLMClient needs at least one route")
        self._routes = list(routes)
        # One breaker per route, in this process (ADR 0041). None turns them off.
        self._breakers = [
            CircuitBreaker(breaker_policy, clock) if breaker_policy is not None else None
            for _ in self._routes
        ]
        self._ledger = ledger
        self._max_retries = max_retries
        self._max_wait = max_wait
        self._sleep = sleep
        self._tracer = tracer or trace.get_tracer("da_agent.llm")
        self._record_content = record_content
        # Outbound requests and tokens per minute per route (ADR 0041). None sets no limit.
        self._limiter = limiter
        # How long the last route waits for its limit. Earlier routes wait only max_wait and then
        # fall back; the last has nothing to fall back to, so queueing beats failing the run.
        self._max_throttle_wait = max(max_throttle_wait, max_wait)
        # Prometheus counters for SLOs and alerts (ADR 0044). None records nothing.
        self._metrics = metrics
        if metrics is not None:
            metrics.prepare([(route.adapter.provider, route.model) for route in self._routes])

    def complete(
        self,
        request: LLMRequest,
        *,
        run_id: str,
        budget: RunBudget | None = None,
        interrupted: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Try each route in order.

        Raises BudgetExceededError, AllRoutesFailedError, or InvalidToolCallError. The last one is
        the model's mistake, so it goes straight back to the caller without a fallback.
        interrupted is asked during waits for a limit or a retry; when it says yes, the wait ends
        with CallInterruptedError, so a stopped run never sits out a long wait (ADR 0042).
        """
        self._check_budget(run_id, budget)
        failures: list[str] = []
        for index, (route, breaker) in enumerate(zip(self._routes, self._breakers, strict=True)):
            last = index == len(self._routes) - 1
            throttle_wait = self._max_throttle_wait if last else self._max_wait
            # A stop during the previous route's call must not start a call on the next one.
            _stop_if_asked(interrupted)
            if breaker is not None and not breaker.allow():
                self._skip(route, run_id)
                failures.append(f"{route.adapter.provider}/{route.model}: circuit open")
                continue
            try:
                return self._complete_with_retries(
                    route, breaker, request, run_id, throttle_wait, interrupted
                )
            except (InvalidToolCallError, CallInterruptedError):
                raise
            except LLMError as error:
                failures.append(f"{route.adapter.provider}/{route.model}: {error}")
            finally:
                self._report_breaker(route, breaker)
        raise AllRoutesFailedError("; ".join(failures))

    def _report_breaker(self, route: Route, breaker: CircuitBreaker | None) -> None:
        if self._metrics is not None and breaker is not None:
            key = route_key(route.adapter.provider, route.model)
            self._metrics.breaker(key, breaker.state != "closed")

    def _check_budget(self, run_id: str, budget: RunBudget | None) -> None:
        if budget is None:
            return
        totals = self._ledger.totals(run_id)
        if budget.max_total_tokens is not None and totals.total_tokens >= budget.max_total_tokens:
            raise BudgetExceededError(f"run {run_id} used {totals.total_tokens} tokens")
        if budget.max_cost_usd is not None and totals.cost_usd >= budget.max_cost_usd:
            raise BudgetExceededError(f"run {run_id} spent ${totals.cost_usd:.4f}")

    def _skip(self, route: Route, run_id: str) -> None:
        with self._tracer.start_as_current_span("llm.route_skipped") as span:
            span.set_attribute("llm.provider", route.adapter.provider)
            span.set_attribute("llm.model_name", route.model)
            span.set_attribute("da_agent.run_id", run_id)
            span.add_event("circuit_open")
        if self._metrics is not None:
            self._metrics.breaker(route_key(route.adapter.provider, route.model), True)

    def _complete_with_retries(
        self,
        route: Route,
        breaker: CircuitBreaker | None,
        request: LLMRequest,
        run_id: str,
        throttle_wait: float,
        interrupted: Callable[[], bool] | None,
    ) -> LLMResponse:
        attempt = 0
        while True:
            _stop_if_asked(interrupted)
            try:
                response = self._attempt(route, request, run_id, throttle_wait, interrupted)
            except ThrottledError:
                # Our own limit, not the provider's health: fall back, never retry or count it. A
                # half-open probe it stopped learned nothing, so the next caller probes.
                if breaker is not None:
                    breaker.release_probe()
                raise
            except (PermanentLLMError, InvalidToolCallError):
                # The provider answered: the request or the configuration is at fault, not the
                # provider's health. A half-open probe that gets this answer closes the breaker.
                if breaker is not None:
                    breaker.record_success()
                raise
            except (RateLimitedError, TransientLLMError) as error:
                if breaker is not None:
                    breaker.record_failure()
                wait = self._wait_before_retry(error, attempt)
                if attempt >= self._max_retries or wait > self._max_wait:
                    raise
                if breaker is not None and not breaker.allow():
                    raise
                self._wait(wait, interrupted)
                # Other calls may have opened the breaker while this one waited.
                if breaker is not None and breaker.state == "open":
                    raise
                attempt += 1
            except Exception:
                # A bug or an interruption says nothing about the provider either.
                if breaker is not None:
                    breaker.release_probe()
                raise
            else:
                if breaker is not None:
                    breaker.record_success()
                return response

    @staticmethod
    def _wait_before_retry(error: LLMError, attempt: int) -> float:
        retry_after = getattr(error, "retry_after", None)
        return retry_after if retry_after is not None else BASE_WAIT_SECONDS * 2**attempt

    def _wait(self, seconds: float, interrupted: Callable[[], bool] | None) -> None:
        """Sleep, in one-second slices when the caller may stop, checking before each."""
        if interrupted is None:
            self._sleep(seconds)
            return
        left = seconds
        while left > 0:
            _stop_if_asked(interrupted)
            step = min(WAIT_SLICE_SECONDS, left)
            self._sleep(step)
            left -= step

    def _attempt(
        self,
        route: Route,
        request: LLMRequest,
        run_id: str,
        throttle_wait: float,
        interrupted: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        with self._tracer.start_as_current_span("llm.complete") as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("llm.provider", route.adapter.provider)
            span.set_attribute("llm.model_name", route.model)
            span.set_attribute("da_agent.run_id", run_id)
            if self._record_content:
                span.set_attribute("input.value", request.model_dump_json(exclude={"tools"}))

            key = route_key(route.adapter.provider, route.model)
            reserved = 0
            started = time.monotonic()
            try:
                if self._limiter is not None:
                    tokens = estimate_tokens(request)
                    wait = self._limiter.acquire(key, tokens, throttle_wait)
                    reserved = tokens
                    span.set_attribute("llm.throttle_wait_seconds", wait)
                    if wait > 0:
                        self._wait(wait, interrupted)
                    started = time.monotonic()
                response = route.adapter.complete(request, route.model)
            except LLMError as error:
                if self._limiter is not None and reserved:
                    if isinstance(error, CallInterruptedError):
                        # Stopped while waiting its turn: the request never went out.
                        self._limiter.release(key, reserved)
                    else:
                        self._limiter.settle(key, reserved, 0)
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                if isinstance(error, ThrottledError):
                    # Refused by our own limit: no call reached the provider.
                    span.set_attribute("da_agent.throttled", True)
                if isinstance(error, CallInterruptedError):
                    raise  # No call went out, so the ledger has nothing to record.
                if self._metrics is not None:
                    self._metrics.call(route.adapter.provider, route.model, error_kind(error))
                self._ledger.record(
                    CostRecord(
                        run_id=run_id, provider=route.adapter.provider, model=route.model,
                        input_tokens=0, output_tokens=0, cached_input_tokens=0, cost_usd=0.0,
                        latency_seconds=time.monotonic() - started, error=error_kind(error),
                    )
                )  # fmt: skip
                raise
            latency = time.monotonic() - started
            if self._limiter is not None:
                self._limiter.settle(key, reserved, response.usage.total_tokens)

            response = response.model_copy(
                update={"provider": route.adapter.provider, "model": route.model}
            )
            # A replayed response keeps its tokens, so budgets behave as in the recorded run.
            cost = 0.0 if response.replayed else route.price.cost(response.usage)
            self._ledger.record(
                CostRecord(
                    run_id=run_id,
                    provider=route.adapter.provider,
                    model=route.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_input_tokens=response.usage.cached_input_tokens,
                    cost_usd=cost,
                    latency_seconds=latency,
                )
            )
            span.set_attribute("llm.token_count.prompt", response.usage.input_tokens)
            span.set_attribute("llm.token_count.completion", response.usage.output_tokens)
            span.set_attribute("llm.token_count.total", response.usage.total_tokens)
            span.set_attribute("da_agent.cost_usd", cost)
            if self._metrics is not None:
                self._metrics.call(
                    route.adapter.provider, route.model, "ok", latency,
                    response.usage.input_tokens, response.usage.output_tokens, cost,
                )  # fmt: skip
            if self._record_content:
                span.set_attribute(
                    "output.value", response.model_dump_json(include={"content", "tool_calls"})
                )
            span.set_status(Status(StatusCode.OK))
            return response
