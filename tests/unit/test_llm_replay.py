"""Record and replay of model outcomes (docs/design/2026-11-20-record-replay.md)."""

import threading
from pathlib import Path

import pytest

from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route
from da_agent.llm.replay import (
    Cassette,
    RecordingAdapter,
    ReplayAdapter,
    ReplayMissError,
    record_routes,
    replay_routes,
    replay_scope,
    request_key,
)
from da_agent.llm.types import (
    InvalidToolCallError,
    LLMRequest,
    LLMResponse,
    Message,
    PermanentLLMError,
    ToolCall,
    TransientLLMError,
    Usage,
)


class ScriptedAdapter:
    def __init__(self, provider: str, script: list) -> None:
        self.provider = provider
        self.script = list(script)
        self.calls = 0
        self.api_key = "sk-very-secret-key"

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def request(*texts: str) -> LLMRequest:
    return LLMRequest(messages=[Message(role="user", content=text) for text in texts])


def reply(text: str = "ok") -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[ToolCall(id="c1", name="run_sql", arguments={"sql": "SELECT 1"})],
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def replayer(path: Path, provider: str = "groq", **kwargs) -> ReplayAdapter:
    return ReplayAdapter(provider, Cassette.load(path), **kwargs)


def test_the_key_changes_with_the_request_model_and_provider() -> None:
    base = request_key("groq", "m", request("a"))

    assert base == request_key("groq", "m", request("a"))
    assert base != request_key("groq", "m", request("b"))
    assert base != request_key("groq", "other", request("a"))
    assert base != request_key("vllm", "m", request("a"))


def test_a_replay_returns_the_recorded_response(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    RecordingAdapter(ScriptedAdapter("groq", [reply("hello")]), path).complete(request("q"), "m")

    replayed = replayer(path).complete(request("q"), "m")

    assert replayed.content == "hello"
    assert replayed.tool_calls == reply().tool_calls
    assert replayed.usage == reply().usage
    assert replayed.replayed is True


def test_errors_that_depend_on_the_request_replay_as_the_same_error(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    inner = ScriptedAdapter("groq", [InvalidToolCallError("bad tool"), PermanentLLMError("413")])
    recorder = RecordingAdapter(inner, path)
    for text, kind in (("a", InvalidToolCallError), ("b", PermanentLLMError)):
        with pytest.raises(kind):
            recorder.complete(request(text), "m")

    replay = replayer(path)
    with pytest.raises(InvalidToolCallError, match="bad tool"):
        replay.complete(request("a"), "m")
    with pytest.raises(PermanentLLMError, match="413") as caught:
        replay.complete(request("b"), "m")
    assert not isinstance(caught.value, ReplayMissError)


def test_network_errors_are_not_recorded(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    recorder = RecordingAdapter(
        ScriptedAdapter("groq", [TransientLLMError("timeout"), reply()]), path
    )
    with pytest.raises(TransientLLMError):
        recorder.complete(request("q"), "m")
    recorder.complete(request("q"), "m")

    replay = replayer(path)
    assert replay.complete(request("q"), "m").content == "ok"


def test_repeats_replay_in_order_and_an_extra_call_misses(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    recorder = RecordingAdapter(ScriptedAdapter("groq", [reply("one"), reply("two")]), path)
    recorder.complete(request("q"), "m")
    recorder.complete(request("q"), "m")

    replay = replayer(path)
    assert [replay.complete(request("q"), "m").content for _ in range(2)] == ["one", "two"]
    with pytest.raises(ReplayMissError):
        replay.complete(request("q"), "m")


def test_a_miss_names_the_first_message_that_differs(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    RecordingAdapter(ScriptedAdapter("groq", [reply()]), path).complete(
        request("system", "question", "tool output"), "m"
    )
    cassette = Cassette.load(path)

    with pytest.raises(ReplayMissError, match=r"message 2 \(user\)"):
        ReplayAdapter("groq", cassette).complete(request("system", "question", "changed"), "m")
    assert (cassette.hits, cassette.misses) == (0, 1)
    assert cassette.miss_details[0]["first_difference"] == 2


def test_live_mode_calls_the_model_on_a_miss_and_records_it(tmp_path: Path) -> None:
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    RecordingAdapter(ScriptedAdapter("groq", [reply("recorded")]), old).complete(request("a"), "m")
    live = ScriptedAdapter("groq", [reply("live")])
    replay = replayer(old, live=live, recorder_path=new)

    assert replay.complete(request("a"), "m").content == "recorded"
    fresh = replay.complete(request("b"), "m")

    assert (fresh.content, fresh.replayed, live.calls) == ("live", False, 1)
    again = replayer(new)
    assert again.complete(request("a"), "m").content == "recorded"
    assert again.complete(request("b"), "m").content == "live"


def test_a_truncated_last_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    RecordingAdapter(ScriptedAdapter("groq", [reply()]), path).complete(request("q"), "m")
    with path.open("a", encoding="utf-8") as file:
        file.write('{"key": "abc", "outc')

    assert replayer(path).complete(request("q"), "m").content == "ok"


def test_the_cassette_holds_no_key(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    RecordingAdapter(ScriptedAdapter("groq", [reply()]), path).complete(request("q"), "m")

    assert "sk-very-secret-key" not in path.read_text(encoding="utf-8")


def test_concurrent_recording_writes_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    count = 40
    recorder = RecordingAdapter(ScriptedAdapter("groq", [reply("x" * 5000)] * count), path)
    threads = [
        threading.Thread(target=recorder.complete, args=(request(f"q{i}"), "m"))
        for i in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    replay = replayer(path)
    assert all(replay.complete(request(f"q{i}"), "m").content for i in range(count))


def test_route_wrappers_record_then_replay_through_the_client(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    price = ModelPrice(input_per_million=1.0, output_per_million=1.0)
    recording = record_routes([Route(ScriptedAdapter("groq", [reply()]), "m", price)], path)
    LLMClient(recording, CostLedger()).complete(request("q"), run_id="r")

    ledger = CostLedger()
    routes = replay_routes([("groq", "m")], Cassette.load(path))
    response = LLMClient(routes, ledger).complete(request("q"), run_id="r")

    assert response.content == "ok"
    totals = ledger.totals()
    assert (totals.input_tokens, totals.output_tokens, totals.cost_usd) == (100, 20, 0.0)


def test_strict_replay_with_two_routes_never_calls_a_model(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    path.write_text("", encoding="utf-8")
    routes = replay_routes([("groq", "a"), ("vllm", "b")], Cassette.load(path))

    with pytest.raises(Exception, match="replay miss"):
        LLMClient(routes, CostLedger()).complete(request("q"), run_id="r")


def test_live_replay_uses_a_later_routes_recording_before_calling_a_model(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    flaky = ScriptedAdapter("groq", [TransientLLMError("down")] * 3)
    backup = ScriptedAdapter("vllm", [reply("from vllm")])
    recording = record_routes([Route(flaky, "a"), Route(backup, "b")], path)
    LLMClient(recording, CostLedger(), sleep=lambda _: None).complete(request("q"), run_id="r")

    cassette = Cassette.load(path)
    live = ScriptedAdapter("groq", [reply("live groq")])
    routes = replay_routes([Route(live, "a"), Route(ScriptedAdapter("vllm", []), "b")], cassette)
    response = LLMClient(routes, CostLedger()).complete(request("q"), run_id="r")

    assert (response.content, live.calls) == ("from vllm", 0)
    assert (cassette.hits, cassette.misses) == (1, 0)


def test_a_miss_on_one_route_answered_by_the_next_is_not_counted(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    RecordingAdapter(ScriptedAdapter("vllm", [reply()]), path).complete(request("q"), "b")
    cassette = Cassette.load(path)

    routes = replay_routes([("groq", "a"), ("vllm", "b")], cassette)
    LLMClient(routes, CostLedger()).complete(request("q"), run_id="r")

    assert (cassette.hits, cassette.misses, cassette.miss_details) == (1, 0, [])


def test_a_retried_question_replays_its_last_attempt_first(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    with replay_scope("q1"):
        RecordingAdapter(ScriptedAdapter("groq", [reply("first try")]), path).complete(
            request("q"), "m"
        )
    with replay_scope("q1"):
        RecordingAdapter(ScriptedAdapter("groq", [reply("second try")]), path).complete(
            request("q"), "m"
        )

    with replay_scope("q1"):
        assert replayer(path).complete(request("q"), "m").content == "second try"


def test_questions_with_the_same_request_keep_their_own_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    for run, text in (("q1", "one"), ("q2", "two")):
        with replay_scope(run):
            RecordingAdapter(ScriptedAdapter("groq", [reply(text)]), path).complete(
                request("same"), "m"
            )

    replay = replayer(path)
    with replay_scope("q2"):
        assert replay.complete(request("same"), "m").content == "two"
    with replay_scope("q1"):
        assert replay.complete(request("same"), "m").content == "one"


def test_error_text_is_recorded_without_keys_or_urls(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    error = PermanentLLMError(
        "Incorrect API key provided: sk-proj-abcdefghijklmnopqrstuvwx. "
        "See https://platform.openai.com/account/api-keys. Consumer api_key:AIzaSyA1234567890abcdef"
    )
    with pytest.raises(PermanentLLMError):
        RecordingAdapter(ScriptedAdapter("groq", [error]), path).complete(request("q"), "m")

    text = path.read_text(encoding="utf-8")
    for secret in ("sk-proj-abcdefghijklmnopqrstuvwx", "platform.openai.com", "AIzaSyA1234567890"):
        assert secret not in text


def test_a_lone_surrogate_in_tool_arguments_records_and_replays(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    odd = LLMResponse(tool_calls=[ToolCall(id="c", name="run_sql", arguments={"sql": "\ud83d"})])
    req = LLMRequest(messages=[Message(role="assistant", tool_calls=odd.tool_calls)])
    RecordingAdapter(ScriptedAdapter("groq", [odd]), path).complete(req, "m")

    assert replayer(path).complete(req, "m").tool_calls == odd.tool_calls


def test_a_rejected_call_records_and_replays_what_the_model_wrote(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    generation = '{"name": "json", "arguments": {}}'
    error = InvalidToolCallError("attempted to call tool 'json'", failed_generation=generation)
    with pytest.raises(InvalidToolCallError):
        RecordingAdapter(ScriptedAdapter("groq", [error]), path).complete(request("q"), "m")

    with pytest.raises(InvalidToolCallError) as replayed:
        replayer(path).complete(request("q"), "m")

    assert replayed.value.failed_generation == generation
