"""LLMClient behaviour: retries, fallback, budget stops, cost records, and spans."""

import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route, RunBudget
from da_agent.llm.types import (
    AllRoutesFailedError,
    BudgetExceededError,
    InvalidToolCallError,
    LLMRequest,
    LLMResponse,
    Message,
    PermanentLLMError,
    RateLimitedError,
    TransientLLMError,
    Usage,
)

REQUEST = LLMRequest(messages=[Message(role="user", content="secret question")])


class ScriptedAdapter:
    def __init__(self, provider: str, script: list) -> None:
        self.provider = provider
        self.script = list(script)
        self.calls = 0

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def reply(text: str = "ok", input_tokens: int = 100, output_tokens: int = 50) -> LLMResponse:
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    return LLMResponse(content=text, usage=usage)


def make_client(*adapters: ScriptedAdapter, **kwargs) -> tuple[LLMClient, CostLedger, list[float]]:
    ledger = CostLedger()
    waits: list[float] = []
    routes = [Route(adapter, f"{adapter.provider}-model") for adapter in adapters]
    return LLMClient(routes, ledger, sleep=waits.append, **kwargs), ledger, waits


def test_returns_the_response_with_provider_and_model() -> None:
    client, _, _ = make_client(ScriptedAdapter("groq", [reply("hello")]))

    response = client.complete(REQUEST, run_id="r1")

    assert response.content == "hello"
    assert (response.provider, response.model) == ("groq", "groq-model")


@pytest.mark.parametrize("failure", [RateLimitedError("slow down"), TransientLLMError("timeout")])
def test_retries_retryable_failures_with_growing_waits(failure: Exception) -> None:
    adapter = ScriptedAdapter("groq", [failure, failure, reply()])
    client, _, waits = make_client(adapter)

    assert client.complete(REQUEST, run_id="r1").content == "ok"
    assert adapter.calls == 3
    assert len(waits) == 2
    assert waits[1] > waits[0]


def test_honours_retry_after() -> None:
    adapter = ScriptedAdapter("groq", [RateLimitedError("slow", retry_after=7.0), reply()])
    client, _, waits = make_client(adapter)

    client.complete(REQUEST, run_id="r1")

    assert waits == [7.0]


def test_falls_back_at_once_when_the_wait_is_too_long() -> None:
    primary = ScriptedAdapter("groq", [RateLimitedError("daily limit", retry_after=3600.0)])
    fallback = ScriptedAdapter("gemini", [reply("from fallback")])
    client, _, waits = make_client(primary, fallback, max_wait=30.0)

    response = client.complete(REQUEST, run_id="r1")

    assert response.provider == "gemini"
    assert primary.calls == 1
    assert waits == []


def test_falls_back_after_retries_run_out() -> None:
    primary = ScriptedAdapter("groq", [TransientLLMError("x")] * 3)
    fallback = ScriptedAdapter("gemini", [reply()])
    client, _, _ = make_client(primary, fallback, max_retries=2)

    assert client.complete(REQUEST, run_id="r1").provider == "gemini"
    assert primary.calls == 3


def test_permanent_failure_skips_retries_and_falls_back() -> None:
    primary = ScriptedAdapter("groq", [PermanentLLMError("bad key")])
    fallback = ScriptedAdapter("gemini", [reply()])
    client, _, waits = make_client(primary, fallback)

    assert client.complete(REQUEST, run_id="r1").provider == "gemini"
    assert primary.calls == 1
    assert waits == []


def test_raises_when_every_route_fails() -> None:
    client, _, _ = make_client(
        ScriptedAdapter("groq", [PermanentLLMError("bad key")]),
        ScriptedAdapter("gemini", [PermanentLLMError("bad key")]),
    )

    with pytest.raises(AllRoutesFailedError, match="groq"):
        client.complete(REQUEST, run_id="r1")


def test_records_tokens_and_cost_per_call() -> None:
    ledger = CostLedger()
    price = ModelPrice(input_per_million=1.0, output_per_million=5.0)
    adapter = ScriptedAdapter("claude", [reply(input_tokens=1000, output_tokens=200)])
    client = LLMClient([Route(adapter, "m", price)], ledger, sleep=lambda _: None)

    client.complete(REQUEST, run_id="r1")

    totals = ledger.totals("r1")
    assert (totals.calls, totals.input_tokens, totals.output_tokens) == (1, 1000, 200)
    assert totals.cost_usd == pytest.approx(0.002)


def test_failed_attempts_are_recorded_by_kind_without_counting_as_calls() -> None:
    primary = ScriptedAdapter(
        "groq",
        [RateLimitedError("slow"), TransientLLMError("timeout"), PermanentLLMError("no"), reply()],
    )
    fallback = ScriptedAdapter("gemini", [InvalidToolCallError("bad tool")])
    client, ledger, _ = make_client(primary, fallback, max_retries=2)

    with pytest.raises(InvalidToolCallError):
        client.complete(REQUEST, run_id="r1")
    client.complete(REQUEST, run_id="r1")

    failed = [(r.provider, r.error) for r in ledger.records() if r.error]
    assert failed == [
        ("groq", "rate_limited"),
        ("groq", "transient"),
        ("groq", "permanent"),
        ("gemini", "invalid_tool_call"),
    ]
    assert all(r.cost_usd == 0 and r.input_tokens == 0 for r in ledger.records() if r.error)
    assert ledger.totals("r1").calls == 1


def test_ledger_keeps_runs_separate_and_sums_overall() -> None:
    client, ledger, _ = make_client(ScriptedAdapter("groq", [reply(), reply()]))

    client.complete(REQUEST, run_id="r1")
    client.complete(REQUEST, run_id="r2")

    assert ledger.totals("r1").calls == 1
    assert ledger.totals().calls == 2


def test_ledger_appends_to_a_jsonl_file(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    adapter = ScriptedAdapter("groq", [reply()])
    client = LLMClient([Route(adapter, "m")], CostLedger(path), sleep=lambda _: None)

    client.complete(REQUEST, run_id="r1")

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["run_id"] == "r1"
    assert record["provider"] == "groq"
    assert record["input_tokens"] == 100


def test_token_budget_stops_the_next_call() -> None:
    adapter = ScriptedAdapter("groq", [reply(input_tokens=100, output_tokens=50), reply()])
    client, _, _ = make_client(adapter)
    budget = RunBudget(max_total_tokens=150)

    client.complete(REQUEST, run_id="r1", budget=budget)
    with pytest.raises(BudgetExceededError):
        client.complete(REQUEST, run_id="r1", budget=budget)

    assert adapter.calls == 1


def test_money_budget_stops_the_next_call() -> None:
    ledger = CostLedger()
    price = ModelPrice(input_per_million=1_000_000.0)
    adapter = ScriptedAdapter("claude", [reply(input_tokens=1), reply()])
    client = LLMClient([Route(adapter, "m", price)], ledger, sleep=lambda _: None)
    budget = RunBudget(max_cost_usd=0.5)

    client.complete(REQUEST, run_id="r1", budget=budget)
    with pytest.raises(BudgetExceededError):
        client.complete(REQUEST, run_id="r1", budget=budget)


def test_budget_applies_per_run() -> None:
    adapter = ScriptedAdapter("groq", [reply(), reply()])
    client, _, _ = make_client(adapter)
    budget = RunBudget(max_total_tokens=150)

    client.complete(REQUEST, run_id="r1", budget=budget)

    assert client.complete(REQUEST, run_id="r2", budget=budget).content == "ok"


def make_traced(*adapters: ScriptedAdapter, **kwargs):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client, _, _ = make_client(*adapters, tracer=provider.get_tracer("test"), **kwargs)
    return client, exporter


def test_span_records_model_tokens_and_cost_without_content_by_default() -> None:
    client, exporter = make_traced(ScriptedAdapter("groq", [reply("private answer")]))

    client.complete(REQUEST, run_id="r1")

    span = exporter.get_finished_spans()[0]
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["llm.model_name"] == "groq-model"
    assert span.attributes["llm.provider"] == "groq"
    assert span.attributes["llm.token_count.prompt"] == 100
    assert span.attributes["llm.token_count.completion"] == 50
    assert span.attributes["da_agent.run_id"] == "r1"
    assert "secret question" not in str(dict(span.attributes))
    assert "private answer" not in str(dict(span.attributes))


def test_span_can_record_content_when_enabled() -> None:
    client, exporter = make_traced(
        ScriptedAdapter("groq", [reply("visible answer")]), record_content=True
    )

    client.complete(REQUEST, run_id="r1")

    attributes = exporter.get_finished_spans()[0].attributes
    assert "secret question" in attributes["input.value"]
    assert "visible answer" in attributes["output.value"]


def test_failed_attempts_appear_as_error_spans() -> None:
    client, exporter = make_traced(
        ScriptedAdapter("groq", [PermanentLLMError("bad key")]),
        ScriptedAdapter("gemini", [reply()]),
    )

    client.complete(REQUEST, run_id="r1")

    spans = {span.attributes["llm.provider"]: span for span in exporter.get_finished_spans()}
    assert not spans["groq"].status.is_ok
    assert spans["gemini"].status.is_ok


def test_cached_input_tokens_use_the_cached_price() -> None:
    price = ModelPrice(input_per_million=1.0, output_per_million=5.0, cached_input_per_million=0.5)
    usage = Usage(input_tokens=1000, output_tokens=200, cached_input_tokens=600)

    # 400 uncached at $1, 600 cached at $0.50, 200 output at $5, per million.
    assert price.cost(usage) == pytest.approx((400 * 1.0 + 600 * 0.5 + 200 * 5.0) / 1_000_000)


def test_cached_tokens_cost_the_full_input_price_when_no_cached_price_is_set() -> None:
    price = ModelPrice(input_per_million=1.0)
    usage = Usage(input_tokens=1000, cached_input_tokens=600)

    assert price.cost(usage) == pytest.approx(1000 / 1_000_000)


def test_invalid_tool_call_goes_back_to_the_caller_without_retry_or_fallback() -> None:
    primary = ScriptedAdapter("groq", [InvalidToolCallError("bad tool name")])
    fallback = ScriptedAdapter("gemini", [reply()])
    client, _, waits = make_client(primary, fallback)

    with pytest.raises(InvalidToolCallError):
        client.complete(REQUEST, run_id="r1")
    assert (primary.calls, fallback.calls, waits) == (1, 0, [])


def test_a_replayed_response_costs_nothing_but_keeps_its_tokens() -> None:
    replayed = reply().model_copy(update={"replayed": True})
    ledger = CostLedger()
    price = ModelPrice(input_per_million=1.0, output_per_million=2.0)
    client = LLMClient([Route(ScriptedAdapter("groq", [replayed]), "m", price)], ledger)

    client.complete(REQUEST, run_id="r1")

    totals = ledger.totals("r1")
    assert (totals.input_tokens, totals.output_tokens, totals.cost_usd) == (100, 50, 0.0)


def test_an_open_breaker_skips_its_route_without_calling_or_waiting() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    primary = ScriptedAdapter("groq", [TransientLLMError("down")] * 4)
    backup = ScriptedAdapter("gemini", [reply("one"), reply("two"), reply("three")])
    client, _, waits = make_client(
        primary, backup, max_retries=1, breaker_policy=BreakerPolicy(failures=4)
    )
    client.complete(REQUEST, run_id="r")
    client.complete(REQUEST, run_id="r")
    calls, slept = primary.calls, len(waits)

    response = client.complete(REQUEST, run_id="r")

    assert response.content == "three"
    assert (primary.calls, len(waits)) == (calls, slept)


def test_every_route_open_fails_at_once() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    primary = ScriptedAdapter("groq", [TransientLLMError("down")])
    client, _, _ = make_client(primary, max_retries=0, breaker_policy=BreakerPolicy(failures=1))
    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")

    with pytest.raises(AllRoutesFailedError, match="circuit open"):
        client.complete(REQUEST, run_id="r")
    assert primary.calls == 1


def test_retries_stop_when_the_breaker_opens() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    primary = ScriptedAdapter("groq", [TransientLLMError("down")] * 6)
    client, _, _ = make_client(primary, max_retries=5, breaker_policy=BreakerPolicy(failures=2))

    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")

    assert primary.calls == 2


def test_model_mistakes_and_permanent_errors_never_open_the_breaker() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    script = [InvalidToolCallError("bad"), PermanentLLMError("413")] * 3 + [reply("ok")]
    primary = ScriptedAdapter("groq", script)
    client, _, _ = make_client(primary, breaker_policy=BreakerPolicy(failures=1))
    for _ in range(6):
        with pytest.raises((InvalidToolCallError, AllRoutesFailedError)):
            client.complete(REQUEST, run_id="r")

    assert client.complete(REQUEST, run_id="r").content == "ok"


def test_a_skipped_route_leaves_a_span_event() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    primary = ScriptedAdapter("groq", [TransientLLMError("down")])
    backup = ScriptedAdapter("gemini", [reply(), reply()])
    client, _, _ = make_client(
        primary, backup, max_retries=0, breaker_policy=BreakerPolicy(failures=1),
        tracer=provider.get_tracer("t"),
    )  # fmt: skip
    client.complete(REQUEST, run_id="r")

    client.complete(REQUEST, run_id="r")

    events = [event.name for span in exporter.get_finished_spans() for event in span.events]
    assert "circuit_open" in events


def test_a_probe_answered_with_a_request_error_closes_the_breaker() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    now = [0.0]
    primary = ScriptedAdapter(
        "groq", [TransientLLMError("down"), PermanentLLMError("413"), reply()]
    )
    client, _, _ = make_client(
        primary, max_retries=0, breaker_policy=BreakerPolicy(1, 30.0), clock=lambda: now[0]
    )
    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")
    now[0] = 30.0
    with pytest.raises(AllRoutesFailedError, match="413"):
        client.complete(REQUEST, run_id="r")

    assert client.complete(REQUEST, run_id="r").content == "ok"


class FakeLimiter:
    def __init__(self, waits: list) -> None:
        self.waits = list(waits)
        self.acquired: list[tuple[str, int]] = []
        self.settled: list[tuple[str, int, int]] = []

    def acquire(self, route_key: str, tokens: int, max_wait: float) -> float:
        self.acquired.append((route_key, tokens))
        step = self.waits.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def settle(self, route_key: str, reserved: int, used: int) -> None:
        self.settled.append((route_key, reserved, used))


def test_the_client_waits_as_the_limiter_says_then_settles_real_usage() -> None:
    limiter = FakeLimiter([2.5])
    client, _, waits = make_client(ScriptedAdapter("groq", [reply()]), limiter=limiter)

    client.complete(REQUEST, run_id="r")

    assert waits == [2.5]
    ((key, reserved),) = limiter.acquired
    assert key == "groq:groq-model"
    assert limiter.settled == [("groq:groq-model", reserved, 150)]


def test_a_throttled_route_falls_back_without_touching_its_breaker() -> None:
    from da_agent.llm.breaker import BreakerPolicy
    from da_agent.llm.types import ThrottledError

    limiter = FakeLimiter([ThrottledError("busy"), 0.0, ThrottledError("busy"), 0.0, 0.0])
    primary = ScriptedAdapter("groq", [reply("primary")])
    backup = ScriptedAdapter("gemini", [reply("backup"), reply("backup")])
    client, ledger, _ = make_client(
        primary, backup, limiter=limiter, breaker_policy=BreakerPolicy(failures=1)
    )

    assert client.complete(REQUEST, run_id="r").content == "backup"
    assert client.complete(REQUEST, run_id="r").content == "backup"
    assert client.complete(REQUEST, run_id="r").content == "primary"
    assert [r.error for r in ledger.records()].count("throttled") == 2


def test_a_failed_call_settles_to_zero() -> None:
    limiter = FakeLimiter([0.0])
    client, _, _ = make_client(ScriptedAdapter("groq", [PermanentLLMError("no")]), limiter=limiter)
    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")

    ((_, reserved, used),) = limiter.settled
    assert used == 0 and reserved > 0


def test_the_span_records_the_throttle_wait() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client, _, _ = make_client(
        ScriptedAdapter("groq", [reply()]), limiter=FakeLimiter([1.5]),
        tracer=provider.get_tracer("t"),
    )  # fmt: skip

    client.complete(REQUEST, run_id="r")

    (span,) = exporter.get_finished_spans()
    assert span.attributes["llm.throttle_wait_seconds"] == 1.5


def test_a_throttled_probe_does_not_leave_the_breaker_half_open() -> None:
    from da_agent.llm.breaker import BreakerPolicy
    from da_agent.llm.types import ThrottledError

    now = [0.0]
    primary = ScriptedAdapter("groq", [TransientLLMError("down"), reply("back")])
    backup = ScriptedAdapter(
        "gemini", [reply("backup"), PermanentLLMError("no"), PermanentLLMError("no")]
    )
    limiter = FakeLimiter([0.0, 0.0, ThrottledError("busy"), 0.0, 0.0, 0.0])
    client, _, _ = make_client(
        primary, backup, max_retries=0, limiter=limiter,
        breaker_policy=BreakerPolicy(1, 30.0), clock=lambda: now[0],
    )  # fmt: skip
    client.complete(REQUEST, run_id="r")  # primary fails and opens, backup answers
    now[0] = 30.0
    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")  # the probe is throttled; no backup reply left

    assert client.complete(REQUEST, run_id="r").content == "back"


def test_an_unexpected_error_in_a_probe_does_not_leave_the_breaker_half_open() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    now = [0.0]
    primary = ScriptedAdapter("groq", [TransientLLMError("down"), RuntimeError("bug"), reply()])
    client, _, _ = make_client(
        primary, max_retries=0, breaker_policy=BreakerPolicy(1, 30.0), clock=lambda: now[0]
    )
    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")
    now[0] = 30.0
    with pytest.raises(RuntimeError):
        client.complete(REQUEST, run_id="r")

    assert client.complete(REQUEST, run_id="r").content == "ok"


def test_the_last_route_waits_longer_for_its_limit_instead_of_failing() -> None:
    from da_agent.llm.ratelimit import MemoryRateLimiter, RouteLimit

    limiter = MemoryRateLimiter({"groq:groq-model": RouteLimit(rpm=1)}, clock=lambda: 0.0)
    client, _, waits = make_client(
        ScriptedAdapter("groq", [reply(), reply()]), limiter=limiter, max_throttle_wait=300.0
    )
    client.complete(REQUEST, run_id="r")

    client.complete(REQUEST, run_id="r")

    assert waits == [pytest.approx(60.0)]


def test_a_route_with_a_fallback_still_falls_back_after_the_short_wait() -> None:
    from da_agent.llm.ratelimit import MemoryRateLimiter, RouteLimit

    limiter = MemoryRateLimiter({"groq:groq-model": RouteLimit(rpm=1)}, clock=lambda: 0.0)
    primary = ScriptedAdapter("groq", [reply("primary")])
    backup = ScriptedAdapter("gemini", [reply("backup")])
    client, _, _ = make_client(primary, backup, limiter=limiter, max_throttle_wait=300.0)
    client.complete(REQUEST, run_id="r")

    assert client.complete(REQUEST, run_id="r").content == "backup"
