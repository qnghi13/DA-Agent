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
