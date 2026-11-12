"""Checks a local model server must pass before an eval run spends GPU hours on it."""

import threading

from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.llm.types import LLMRequest, LLMResponse, ToolCall, Usage
from eval.gpu.preflight import check_repeatable, check_tool_call, measure_throughput


class FakeServer:
    provider = "fake"

    def __init__(self, *, tool_name: str = "run_sql", drift: bool = False) -> None:
        self.tool_name = tool_name
        self.drift = drift
        self.calls = 0
        self.lock = threading.Lock()

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        with self.lock:
            self.calls += 1
            call = self.calls
        usage = Usage(input_tokens=20, output_tokens=10)
        if request.tools:
            tool = ToolCall(id="c", name=self.tool_name, arguments={"sql": "SELECT 1"})
            return LLMResponse(tool_calls=[tool], usage=usage)
        text = f"answer {call}" if self.drift else "answer"
        return LLMResponse(content=text, usage=usage)


def client(server: FakeServer) -> LLMClient:
    return LLMClient([Route(server, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)


def test_a_server_that_calls_the_offered_tool_passes() -> None:
    result = check_tool_call(client(FakeServer()))
    assert result["ok"] is True


def test_a_server_that_answers_in_text_or_calls_another_tool_fails() -> None:
    result = check_tool_call(client(FakeServer(tool_name="other")))
    assert result["ok"] is False
    assert "other" in result["detail"]


def test_same_answers_alone_and_in_a_busy_batch_pass() -> None:
    """The challenge re-runs the image; a server whose output depends on its batch would drift."""
    result = check_repeatable(client(FakeServer()), repeats=2, busy=3)
    assert result["ok"] is True


def test_answers_that_change_between_runs_fail() -> None:
    result = check_repeatable(client(FakeServer(drift=True)), repeats=2, busy=3)
    assert result["ok"] is False
    assert result["distinct"] > 1


def test_throughput_counts_output_tokens_over_wall_time() -> None:
    server = FakeServer()
    result = measure_throughput(client(server), requests=6, concurrency=3)
    assert server.calls == 6
    assert result["output_tokens"] == 60
    assert result["requests"] == 6
    assert result["output_tokens_per_second"] > 0
