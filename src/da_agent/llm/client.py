"""LLMClient: the only way nodes reach a model. Owns retries, fallback, budgets, and costs.

See docs/adr/0002-custom-llm-client.md.
"""

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, Tracer

from da_agent.llm.breaker import BreakerPolicy, CircuitBreaker
from da_agent.llm.ratelimit import RateLimiter, estimate_tokens, route_key
from da_agent.llm.types import (
    AllRoutesFailedError,
    BudgetExceededError,
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

BASE_WAIT_SECONDS = 1.0
DEFAULT_BREAKER = BreakerPolicy()


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

    def complete(
        self, request: LLMRequest, *, run_id: str, budget: RunBudget | None = None
    ) -> LLMResponse:
        """Try each route in order.

        Raises BudgetExceededError, AllRoutesFailedError, or InvalidToolCallError. The last one is
        the model's mistake, so it goes straight back to the caller without a fallback.
        """
        self._check_budget(run_id, budget)
        failures: list[str] = []
        for route, breaker in zip(self._routes, self._breakers, strict=True):
            if breaker is not None and not breaker.allow():
                self._skip(route, run_id)
                failures.append(f"{route.adapter.provider}/{route.model}: circuit open")
                continue
            try:
                return self._complete_with_retries(route, breaker, request, run_id)
            except InvalidToolCallError:
                raise
            except LLMError as error:
                failures.append(f"{route.adapter.provider}/{route.model}: {error}")
        raise AllRoutesFailedError("; ".join(failures))

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

    def _complete_with_retries(
        self, route: Route, breaker: CircuitBreaker | None, request: LLMRequest, run_id: str
    ) -> LLMResponse:
        attempt = 0
        while True:
            try:
                response = self._attempt(route, request, run_id)
            except ThrottledError:
                # Our own limit, not the provider's health: fall back, never retry or count it.
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
                self._sleep(wait)
                attempt += 1
            else:
                if breaker is not None:
                    breaker.record_success()
                return response

    @staticmethod
    def _wait_before_retry(error: LLMError, attempt: int) -> float:
        retry_after = getattr(error, "retry_after", None)
        return retry_after if retry_after is not None else BASE_WAIT_SECONDS * 2**attempt

    def _attempt(self, route: Route, request: LLMRequest, run_id: str) -> LLMResponse:
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
                    wait = self._limiter.acquire(key, tokens, self._max_wait)
                    reserved = tokens
                    span.set_attribute("llm.throttle_wait_seconds", wait)
                    if wait > 0:
                        self._sleep(wait)
                    started = time.monotonic()
                response = route.adapter.complete(request, route.model)
            except LLMError as error:
                if self._limiter is not None and reserved:
                    self._limiter.settle(key, reserved, 0)
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
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
            if self._record_content:
                span.set_attribute(
                    "output.value", response.model_dump_json(include={"content", "tool_calls"})
                )
            span.set_status(Status(StatusCode.OK))
            return response
