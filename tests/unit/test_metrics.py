"""Prometheus metrics for SLOs (ADR 0044)."""

import pytest
from prometheus_client import CollectorRegistry

from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route
from da_agent.llm.types import InvalidToolCallError, LLMRequest, LLMResponse, Message, Usage
from da_agent.telemetry.metrics import LLMMetrics, WorkerMetrics, run_outcome

REQUEST = LLMRequest(messages=[Message(role="user", content="q")])


def value(registry: CollectorRegistry, name: str, **labels: str) -> float:
    found = registry.get_sample_value(name, labels)
    return 0.0 if found is None else found


@pytest.mark.parametrize(
    ("status", "result", "outcome"),
    [
        ("succeeded", {"stop_reason": "answered", "answer": {"kind": "number"}}, "answered"),
        ("succeeded", {"stop_reason": "answered", "answer": {"kind": "unanswerable"}}, "abstained"),
        ("succeeded", {"stop_reason": "unverified"}, "unverified"),
        ("succeeded", {"stop_reason": "unreadable_values"}, "unverified"),
        ("succeeded", {"stop_reason": "llm_error"}, "model_error"),
        ("succeeded", {"stop_reason": "invalid_tool_call"}, "model_error"),
        ("succeeded", {"stop_reason": "no_tool_call"}, "model_error"),
        ("succeeded", {"stop_reason": "budget_exceeded"}, "budget_exceeded"),
        ("succeeded", {"stop_reason": "iteration_limit"}, "iteration_limit"),
        ("succeeded", {"stop_reason": "needs_clarification"}, "needs_clarification"),
        ("succeeded", {"stop_reason": "deadline_exceeded"}, "deadline_exceeded"),
        ("succeeded", {"stop_reason": "something new"}, "other"),
        ("cancelled", {"stop_reason": "cancelled"}, "cancelled"),
        ("failed", None, "failed"),
    ],
)
def test_outcomes_group_like_the_ops_panel(status: str, result: dict | None, outcome: str) -> None:
    assert run_outcome(status, result) == outcome


def test_worker_metrics_count_runs_attempts_and_durations() -> None:
    registry = CollectorRegistry()
    metrics = WorkerMetrics(registry)

    metrics.run_finished("answered")
    metrics.attempt("run", "succeeded")
    metrics.run_duration(12.0)
    metrics.queue_wait(3.0)

    assert value(registry, "da_agent_runs_total", outcome="answered") == 1
    assert value(registry, "da_agent_job_attempts_total", kind="run", result="succeeded") == 1
    assert value(registry, "da_agent_run_duration_seconds_bucket", le="20.0") == 1
    assert value(registry, "da_agent_run_duration_seconds_bucket", le="10.0") == 0
    assert value(registry, "da_agent_queue_wait_seconds_count") == 1


def test_a_broken_metric_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = WorkerMetrics(CollectorRegistry())
    monkeypatch.setattr(metrics, "_runs", None)

    metrics.run_finished("answered")


class Scripted:
    provider = "groq"

    def __init__(self, script: list) -> None:
        self.script = list(script)

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_llm_calls_are_counted_by_result_with_tokens_and_cost() -> None:
    registry = CollectorRegistry()
    reply = LLMResponse(content="ok", usage=Usage(input_tokens=1000, output_tokens=100))
    price = ModelPrice(input_per_million=1.0, output_per_million=2.0)
    route = Route(Scripted([reply, InvalidToolCallError("bad")]), "m", price)
    client = LLMClient([route], CostLedger(), metrics=LLMMetrics(registry))

    client.complete(REQUEST, run_id="r")
    with pytest.raises(InvalidToolCallError):
        client.complete(REQUEST, run_id="r")

    labels = {"provider": "groq", "model": "m"}
    assert value(registry, "da_agent_llm_calls_total", **labels, result="ok") == 1
    assert value(registry, "da_agent_llm_calls_total", **labels, result="invalid_tool_call") == 1
    assert value(registry, "da_agent_llm_call_duration_seconds_count", **labels) == 1
    assert value(registry, "da_agent_llm_tokens_total", direction="input") == 1000
    assert value(registry, "da_agent_llm_tokens_total", direction="output") == 100
    assert value(registry, "da_agent_llm_cost_usd_total") == pytest.approx(0.0012)


def test_an_open_breaker_shows_as_a_gauge() -> None:
    from da_agent.llm.breaker import BreakerPolicy
    from da_agent.llm.types import AllRoutesFailedError, TransientLLMError

    registry = CollectorRegistry()
    route = Route(Scripted([TransientLLMError("down")]), "m")
    client = LLMClient(
        [route], CostLedger(), max_retries=0, breaker_policy=BreakerPolicy(failures=1),
        metrics=LLMMetrics(registry),
    )  # fmt: skip

    with pytest.raises(AllRoutesFailedError):
        client.complete(REQUEST, run_id="r")

    assert value(registry, "da_agent_breaker_open", route="groq:m") == 1


def test_the_process_metrics_are_made_once() -> None:
    from da_agent.telemetry.metrics import llm_metrics, worker_metrics

    assert worker_metrics() is worker_metrics()
    assert llm_metrics() is llm_metrics()


def test_every_labelled_series_starts_at_zero_so_the_first_event_counts() -> None:
    from da_agent.telemetry.metrics import ATTEMPT_RESULTS, CALL_RESULTS, JOB_KINDS, RUN_OUTCOMES

    registry = CollectorRegistry()
    WorkerMetrics(registry)
    llm = LLMMetrics(registry)

    llm.prepare([("groq", "m")])

    for outcome in RUN_OUTCOMES:
        assert registry.get_sample_value("da_agent_runs_total", {"outcome": outcome}) == 0
    for kind in JOB_KINDS:
        for result in ATTEMPT_RESULTS:
            labels = {"kind": kind, "result": result}
            assert registry.get_sample_value("da_agent_job_attempts_total", labels) == 0
    assert "throttled" in CALL_RESULTS and "ok" in CALL_RESULTS
    for result in CALL_RESULTS:
        labels = {"provider": "groq", "model": "m", "result": result}
        assert registry.get_sample_value("da_agent_llm_calls_total", labels) == 0
    assert registry.get_sample_value("da_agent_breaker_open", {"route": "groq:m"}) == 0
