"""Adapters run through the real provider SDKs against a fake HTTP transport, so nothing leaves."""

import json
from collections.abc import Callable

import httpx
import httpx2
import pytest

from da_agent.llm.adapters.gemini import GeminiAdapter
from da_agent.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from da_agent.llm.types import (
    InvalidToolCallError,
    LLMRequest,
    Message,
    PermanentLLMError,
    RateLimitedError,
    ToolCall,
    ToolSpec,
    TransientLLMError,
)

TOOL = ToolSpec(
    name="run_sql",
    description="Run a query",
    parameters={"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
)
CONVERSATION = [
    Message(role="system", content="Be careful"),
    Message(role="user", content="How many?"),
    Message(
        role="assistant",
        tool_calls=[ToolCall(id="call_1", name="run_sql", arguments={"sql": "SELECT 1"})],
    ),
    Message(role="tool", tool_call_id="call_1", tool_name="run_sql", content='{"rows": [[1]]}'),
]
REQUEST = LLMRequest(messages=CONVERSATION, tools=[TOOL], max_output_tokens=100)

Handler = Callable[[httpx.Request], httpx.Response]
OpenAIHandler = Callable[[httpx2.Request], httpx2.Response]


def openai_adapter(handler: OpenAIHandler) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        provider="groq",
        base_url="https://llm.test/v1",
        api_key="test-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def openai_reply(message: dict, usage: dict | None = None) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_openai_sends_neutral_messages_in_wire_format() -> None:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return openai_reply({"role": "assistant", "content": "done"})

    response = openai_adapter(handler).complete(REQUEST, "llama")

    assert seen["model"] == "llama"
    assert seen["max_tokens"] == 100
    assert seen["tools"][0]["function"]["name"] == "run_sql"
    assert [message["role"] for message in seen["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    call = seen["messages"][2]["tool_calls"][0]
    assert call["id"] == "call_1"
    assert json.loads(call["function"]["arguments"]) == {"sql": "SELECT 1"}
    assert seen["messages"][3]["tool_call_id"] == "call_1"
    assert response.content == "done"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (10, 5)


def tool_reply(arguments: str) -> httpx2.Response:
    return openai_reply(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": arguments},
                }
            ],
        }
    )


def test_openai_parses_tool_calls() -> None:
    adapter = openai_adapter(lambda _: tool_reply('{"sql": "SELECT 2"}'))

    call = adapter.complete(REQUEST, "m").tool_calls[0]

    assert (call.id, call.name, call.arguments) == ("call_9", "run_sql", {"sql": "SELECT 2"})
    assert call.arguments_error is None


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_openai_reports_bad_tool_arguments_instead_of_crashing(arguments: str) -> None:
    call = openai_adapter(lambda _: tool_reply(arguments)).complete(REQUEST, "m").tool_calls[0]

    assert call.arguments == {}
    assert call.arguments_error


def test_openai_replays_assistant_text_with_no_tools() -> None:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return openai_reply({"role": "assistant", "content": "done"})

    openai_adapter(handler).complete(
        LLMRequest(messages=[Message(role="assistant", content="Earlier")]), "m"
    )

    assert "tools" not in seen
    assert "tool_calls" not in seen["messages"][0]


def test_openai_maps_rate_limit_and_retry_after() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, headers={"retry-after": "12"}, json={"error": {"message": "x"}})

    with pytest.raises(RateLimitedError) as caught:
        openai_adapter(handler).complete(REQUEST, "m")

    assert caught.value.retry_after == 12.0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, PermanentLLMError),
        (400, PermanentLLMError),
        (413, PermanentLLMError),
        (500, TransientLLMError),
        (503, TransientLLMError),
    ],
)
def test_openai_maps_status_codes(status: int, expected: type[Exception]) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "x"}})

    with pytest.raises(expected):
        openai_adapter(handler).complete(REQUEST, "m")


def test_openai_reports_a_malformed_tool_call_as_invalid_tool_call() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400,
            json={
                "error": {
                    "message": "attempted to call tool 'submit_answer<|channel|>x'",
                    "code": "tool_use_failed",
                }
            },
        )

    with pytest.raises(InvalidToolCallError, match="submit_answer"):
        openai_adapter(handler).complete(REQUEST, "m")


def test_openai_reads_cached_prompt_tokens() -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 64},
    }

    def handler(_: httpx2.Request) -> httpx2.Response:
        return openai_reply({"role": "assistant", "content": "ok"}, usage)

    response = openai_adapter(handler).complete(REQUEST, "m")

    assert response.usage.input_tokens == 100
    assert response.usage.cached_input_tokens == 64


def test_openai_maps_timeouts_and_connection_errors() -> None:
    def timeout(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    def refused(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    for handler in (timeout, refused):
        with pytest.raises(TransientLLMError):
            openai_adapter(handler).complete(REQUEST, "m")


def test_openai_treats_empty_choices_as_retryable() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [],
            },
        )

    with pytest.raises(TransientLLMError):
        openai_adapter(handler).complete(REQUEST, "m")


def gemini_adapter(handler: Handler) -> GeminiAdapter:
    return GeminiAdapter(api_key="test-key", transport=httpx.MockTransport(handler))


def gemini_reply(parts: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": parts}}],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 7,
                "thoughtsTokenCount": 3,
                "cachedContentTokenCount": 4,
            },
        },
    )


def test_gemini_sends_neutral_messages_in_wire_format() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return gemini_reply([{"text": "done"}])

    response = gemini_adapter(handler).complete(REQUEST, "gemini-x")

    body = seen["body"]
    assert "gemini-x" in seen["url"]
    assert body["systemInstruction"]["parts"][0]["text"] == "Be careful"
    assert [content["role"] for content in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][1]["parts"][0]["functionCall"]["args"] == {"sql": "SELECT 1"}
    result = body["contents"][2]["parts"][0]["functionResponse"]
    assert result["name"] == "run_sql"
    assert result["response"] == {"result": '{"rows": [[1]]}'}
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "run_sql"
    assert body["generationConfig"]["maxOutputTokens"] == 100
    assert response.content == "done"
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 10  # visible output plus thinking tokens
    assert response.usage.cached_input_tokens == 4


def test_gemini_merges_consecutive_tool_results_into_one_turn() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return gemini_reply([{"text": "done"}])

    messages = [
        Message(role="user", content="Q"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="a", name="x"), ToolCall(id="b", name="y")],
        ),
        Message(role="tool", tool_call_id="a", tool_name="x", content="1"),
        Message(role="tool", tool_call_id="b", tool_name="y", content="2"),
    ]
    gemini_adapter(handler).complete(LLMRequest(messages=messages), "m")

    assert len(seen["contents"]) == 3
    assert len(seen["contents"][2]["parts"]) == 2


def test_gemini_parses_calls_and_replays_the_thought_signature() -> None:
    signature = "c2lnbmF0dXJl"
    first = gemini_reply(
        [
            {"text": "hidden", "thought": True},
            {
                "functionCall": {"name": "run_sql", "args": {"sql": "SELECT 3"}},
                "thoughtSignature": signature,
            },
        ]
    )
    response = gemini_adapter(lambda _: first).complete(REQUEST, "m")

    call = response.tool_calls[0]
    assert (call.name, call.arguments) == ("run_sql", {"sql": "SELECT 3"})
    assert call.id
    assert response.content is None

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return gemini_reply([{"text": "done"}])

    replay = [
        Message(role="user", content="Q"),
        Message(role="assistant", tool_calls=response.tool_calls),
        Message(role="tool", tool_call_id=call.id, tool_name="run_sql", content="1"),
    ]
    gemini_adapter(handler).complete(LLMRequest(messages=replay), "m")

    assert seen["contents"][1]["parts"][0]["thoughtSignature"] == signature


def test_gemini_gives_distinct_ids_to_calls_without_one() -> None:
    reply = gemini_reply(
        [
            {"functionCall": {"name": "a", "args": {}}},
            {"functionCall": {"name": "a", "args": {}}},
        ]
    )

    calls = gemini_adapter(lambda _: reply).complete(REQUEST, "m").tool_calls

    assert calls[0].id != calls[1].id


def test_gemini_handles_a_blocked_response_with_no_candidates() -> None:
    reply = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    response = gemini_adapter(lambda _: reply).complete(REQUEST, "m")

    assert response.content is None
    assert response.tool_calls == []


def test_gemini_reads_the_retry_delay_from_a_list_shaped_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        detail = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "9s"}
        return httpx.Response(
            429, json=[{"error": {"code": 429, "message": "quota", "details": [detail]}}]
        )

    with pytest.raises(RateLimitedError) as caught:
        gemini_adapter(handler).complete(REQUEST, "m")

    assert caught.value.retry_after == 9.0


def test_gemini_maps_rate_limit_and_its_retry_delay() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": "quota",
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}
                    ],
                }
            },
        )

    with pytest.raises(RateLimitedError) as caught:
        gemini_adapter(handler).complete(REQUEST, "m")

    assert caught.value.retry_after == 37.0


@pytest.mark.parametrize(
    ("status", "expected"),
    [(400, PermanentLLMError), (403, PermanentLLMError), (500, TransientLLMError)],
)
def test_gemini_maps_status_codes(status: int, expected: type[Exception]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": status, "message": "x"}})

    with pytest.raises(expected):
        gemini_adapter(handler).complete(REQUEST, "m")


def test_gemini_maps_timeouts() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TransientLLMError):
        gemini_adapter(handler).complete(REQUEST, "m")
