"""Adapter for the Gemini API, using the native SDK."""

import base64
import re
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from da_agent.llm.types import (
    LLMRequest,
    LLMResponse,
    Message,
    PermanentLLMError,
    RateLimitedError,
    ToolCall,
    TransientLLMError,
    Usage,
)

SIGNATURE_KEY = "thought_signature"


def _call_to_part(call: ToolCall) -> types.Part:
    signature = call.provider_data.get(SIGNATURE_KEY)
    return types.Part(
        function_call=types.FunctionCall(name=call.name, args=call.arguments),
        thought_signature=base64.b64decode(signature) if signature else None,
    )


def _to_contents(messages: list[Message]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            part = types.Part(
                function_response=types.FunctionResponse(
                    name=message.tool_name, response={"result": message.content}
                )
            )
            # Gemini expects all results of one model turn together in a single user turn.
            previous = contents[-1] if contents else None
            if previous and previous.parts and previous.parts[-1].function_response:
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
            continue

        parts = [types.Part(text=message.content)] if message.content else []
        parts.extend(_call_to_part(call) for call in message.tool_calls)
        role = "model" if message.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _parse_parts(parts: list[types.Part]) -> tuple[str | None, list[ToolCall]]:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for part in parts:
        if part.thought:
            continue
        if part.text:
            texts.append(part.text)
        if part.function_call:
            function = part.function_call
            data: dict[str, Any] = {}
            if part.thought_signature:
                data[SIGNATURE_KEY] = base64.b64encode(part.thought_signature).decode("ascii")
            calls.append(
                ToolCall(
                    id=function.id or f"call_{len(calls)}_{function.name}",
                    name=function.name or "",
                    arguments=function.args or {},
                    provider_data=data,
                )
            )
    return "".join(texts) or None, calls


def _retry_delay(error: errors.APIError) -> float | None:
    body = error.details[0] if isinstance(error.details, list) and error.details else error.details
    details = body.get("error", {}).get("details", []) if isinstance(body, dict) else []
    for detail in details:
        match = re.fullmatch(r"([0-9.]+)s", str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


class GeminiAdapter:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        client_args = {"transport": transport} if transport else None
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout * 1000), client_args=client_args),
        )

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        system = "\n\n".join(m.content or "" for m in request.messages if m.role == "system")
        tools = None
        if request.tools:
            declarations = [
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters,
                )
                for tool in request.tools
            ]
            tools = [types.Tool(function_declarations=declarations)]
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            tools=tools,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
        )
        try:
            raw = self._client.models.generate_content(
                model=model, contents=_to_contents(request.messages), config=config
            )
        except errors.ClientError as error:
            if error.code == 429:
                raise RateLimitedError(str(error), retry_after=_retry_delay(error)) from error
            raise PermanentLLMError(str(error)) from error
        except errors.ServerError as error:
            raise TransientLLMError(str(error)) from error
        except httpx.TransportError as error:
            raise TransientLLMError(str(error)) from error

        candidate = raw.candidates[0] if raw.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else None
        content, calls = _parse_parts(parts or [])
        usage = raw.usage_metadata
        return LLMResponse(
            content=content,
            tool_calls=calls,
            usage=Usage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                # Thinking tokens are billed as output.
                output_tokens=(
                    (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                    if usage
                    else 0
                ),
                cached_input_tokens=(usage.cached_content_token_count or 0) if usage else 0,
            ),
        )
