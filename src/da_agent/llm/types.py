"""Provider-neutral request, response, and error types for the LLM layer."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set when the model produced arguments that are not a JSON object.
    arguments_error: str | None = None
    # Opaque data a provider needs back on the next turn, such as a Gemini thought signature.
    provider_data: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_output_tokens: int = 2048
    temperature: float = 0.0


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    # Served from a recording (da_agent.llm.replay), so it cost nothing.
    replayed: bool = False
    # Calls the provider rejected for a garbled tool name, which the adapter read back as calls
    # to the offered tool the name stands for (ADR 0049).
    repaired_tool_calls: int = 0


class LLMError(Exception):
    """Base class for failures in the LLM layer."""


class RateLimitedError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ThrottledError(RateLimitedError):
    """Our own outbound limit refused the call before it went out (ADR 0041). The client falls back
    to the next route; it never counts toward a circuit breaker."""


class TransientLLMError(LLMError):
    """A timeout, connection failure, or server error that may pass on retry."""


class PermanentLLMError(LLMError):
    """A failure that retrying cannot fix, such as a bad key or a rejected request."""


class InvalidToolCallError(LLMError):
    """The provider rejected the model's tool call, for example a malformed tool name.

    The model caused it, so retrying the same request or another route does not help. The caller
    should tell the model what went wrong and let it try again. failed_generation is what the
    model wrote, when the provider returns it: for diagnosis, never shown to the model.
    """

    def __init__(self, message: str, failed_generation: str | None = None) -> None:
        super().__init__(message)
        self.failed_generation = failed_generation


class CallInterruptedError(LLMError):
    """The caller asked to stop while the client waited for a limit or a retry (ADR 0042). It
    goes straight back to the caller: no retry, no fallback, and no ledger row."""


class AllRoutesFailedError(LLMError):
    """Every configured provider failed."""


class BudgetExceededError(LLMError):
    """The run has used up its token or money limit."""


class LLMAdapter(Protocol):
    """Translates between the neutral types and one provider API."""

    provider: str

    def complete(self, request: LLMRequest, model: str) -> LLMResponse: ...
