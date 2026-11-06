"""The minimal agent: a bounded tool loop. The full LangGraph workflow replaces it on Days 7-10.

Routing and limits are code. The model chooses queries and the final answer.
"""

import json

from opentelemetry import trace
from opentelemetry.trace import Tracer

from da_agent.agent.answer import (
    AgentAnswer,
    AgentResult,
    InvalidAnswerError,
    ToolStep,
    parse_answer,
)
from da_agent.agent.loop_types import AgentLimits, Document
from da_agent.agent.prompts import (
    NUDGE,
    build_system_prompt,
    build_user_prompt,
    invalid_tool_call_notice,
)
from da_agent.agent.rules import RuleIndex
from da_agent.agent.tools import SUBMIT_ANSWER, Dataset, execute_tool, tool_specs
from da_agent.llm.client import LLMClient
from da_agent.llm.types import (
    BudgetExceededError,
    InvalidToolCallError,
    LLMError,
    LLMRequest,
    Message,
    ToolCall,
)

__all__ = ["AgentLimits", "Document", "run_agent"]


def _run_tool(
    call: ToolCall, dataset: Dataset, rules: RuleIndex, tracer: Tracer
) -> tuple[str, ToolStep, AgentAnswer | None]:
    with tracer.start_as_current_span(f"tool.{call.name}") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", call.name)
        answer = None
        if call.name == SUBMIT_ANSWER and not call.arguments_error:
            try:
                answer = parse_answer(call.arguments)
                content, step = '{"status": "recorded"}', ToolStep(tool=call.name, ok=True)
            except InvalidAnswerError as error:
                content = json.dumps({"error": str(error)}, ensure_ascii=False)
                step = ToolStep(tool=call.name, ok=False, error=str(error))
        else:
            content, step = execute_tool(call, dataset, rules)
        if step.sql:
            span.set_attribute("da_agent.sql", step.sql)
        span.set_attribute("da_agent.tool_ok", step.ok)
        return content, step, answer


def run_agent(
    question: str,
    *,
    as_of: str,
    dataset: Dataset,
    documents: list[Document],
    llm: LLMClient,
    run_id: str,
    limits: AgentLimits | None = None,
    tracer: Tracer | None = None,
) -> AgentResult:
    """Answer one question. Limits and provider failures end the run without raising."""
    limits = limits or AgentLimits()
    tracer = tracer or trace.get_tracer("da_agent.agent")
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("da_agent.run_id", run_id)
        result = _loop(question, as_of, dataset, documents, llm, run_id, limits, tracer)
        span.set_attribute("da_agent.stop_reason", result.stop_reason)
        span.set_attribute("da_agent.iterations", result.iterations)
        return result


def _loop(
    question: str,
    as_of: str,
    dataset: Dataset,
    documents: list[Document],
    llm: LLMClient,
    run_id: str,
    limits: AgentLimits,
    tracer: Tracer,
) -> AgentResult:
    messages = [
        Message(role="system", content=build_system_prompt(documents)),
        Message(role="user", content=build_user_prompt(question, as_of)),
    ]
    steps: list[ToolStep] = []
    nudges = 0
    rules = RuleIndex(documents)
    tools = tool_specs(rules, getattr(dataset, "dialect", "SQLite"))

    for iteration in range(1, limits.max_iterations + 1):
        request = LLMRequest(messages=messages, tools=tools)
        try:
            response = llm.complete(request, run_id=run_id, budget=limits.budget)
        except BudgetExceededError:
            return AgentResult(stop_reason="budget_exceeded", steps=steps, iterations=iteration - 1)
        except InvalidToolCallError as error:
            # Shares the nudge allowance: both are turns where the model produced no usable call.
            if nudges >= limits.max_nudges:
                return AgentResult(
                    stop_reason="invalid_tool_call",
                    steps=steps,
                    iterations=iteration,
                    error=str(error),
                )
            nudges += 1
            notice = invalid_tool_call_notice([tool.name for tool in tools])
            messages.append(Message(role="user", content=notice))
            continue
        except LLMError as error:
            return AgentResult(
                stop_reason="llm_error", steps=steps, iterations=iteration - 1, error=str(error)
            )

        messages.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )
        if not response.tool_calls:
            if nudges >= limits.max_nudges:
                return AgentResult(stop_reason="no_tool_call", steps=steps, iterations=iteration)
            nudges += 1
            messages.append(Message(role="user", content=NUDGE))
            continue

        answer = None
        for call in response.tool_calls:
            content, step, submitted = _run_tool(call, dataset, rules, tracer)
            steps.append(step)
            answer = answer or submitted
            messages.append(
                Message(role="tool", content=content, tool_call_id=call.id, tool_name=call.name)
            )
        if answer is not None:
            return AgentResult(answer=answer, steps=steps, iterations=iteration)

    return AgentResult(stop_reason="iteration_limit", steps=steps, iterations=limits.max_iterations)
