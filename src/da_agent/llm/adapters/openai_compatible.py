"""Adapter for OpenAI-compatible chat endpoints: Groq, vLLM, Ollama, DeepSeek."""

import json
from typing import Any

import httpx2
import openai

from da_agent.llm.types import (
    InvalidToolCallError,
    LLMRequest,
    LLMResponse,
    Message,
    PermanentLLMError,
    RateLimitedError,
    ToolCall,
    TransientLLMError,
    Usage,
)


def _to_wire_message(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _parse_tool_call(raw: Any) -> ToolCall:
    try:
        arguments = json.loads(raw.function.arguments or "{}")
    except json.JSONDecodeError as error:
        return ToolCall(
            id=raw.id, name=raw.function.name, arguments_error=f"invalid JSON: {error.msg}"
        )
    if not isinstance(arguments, dict):
        return ToolCall(
            id=raw.id, name=raw.function.name, arguments_error="arguments must be a JSON object"
        )
    return ToolCall(id=raw.id, name=raw.function.name, arguments=arguments)


def _retry_after(error: openai.APIStatusError) -> float | None:
    try:
        return float(error.response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _is_failed_tool_generation(error: openai.APIStatusError) -> bool:
    """Groq returns 400 when the model writes a malformed tool call, such as a bad tool name, or
    output it cannot parse, which it returns as failed_generation."""
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)
    if not isinstance(details, dict):
        return False
    return details.get("code") == "tool_use_failed" or "failed_generation" in details


def _cached_tokens(usage: Any) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cached_tokens", None) or 0


class OpenAICompatibleAdapter:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        http_client: httpx2.Client | None = None,
        timeout: float = 60.0,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout
        # Provider-specific request fields, such as OpenRouter's provider routing.
        self._extra_body = dict(extra_body or {})
        # Retries belong to LLMClient, so the SDK must not add its own.
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client,
            timeout=timeout,
            max_retries=0,
        )

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        # Typed as Any because the SDK's message types are exact shapes that we build dynamically.
        messages: Any = [_to_wire_message(message) for message in request.messages]
        options: dict[str, Any] = {}
        if self._extra_body:
            options["extra_body"] = self._extra_body
        if request.tools:
            options["tools"] = [
                {"type": "function", "function": tool.model_dump()} for tool in request.tools
            ]
        try:
            raw = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
                **options,
            )
        except openai.RateLimitError as error:
            raise RateLimitedError(str(error), retry_after=_retry_after(error)) from error
        except (openai.APITimeoutError, openai.APIConnectionError) as error:
            raise TransientLLMError(str(error)) from error
        except openai.APIStatusError as error:
            if _is_failed_tool_generation(error):
                raise InvalidToolCallError(str(error)) from error
            if error.status_code >= 500:
                raise TransientLLMError(str(error)) from error
            raise PermanentLLMError(str(error)) from error

        if not raw.choices:
            raise TransientLLMError("response has no choices")
        message = raw.choices[0].message
        usage = raw.usage
        return LLMResponse(
            content=message.content,
            tool_calls=[_parse_tool_call(call) for call in message.tool_calls or []],
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cached_input_tokens=_cached_tokens(usage),
            ),
        )
