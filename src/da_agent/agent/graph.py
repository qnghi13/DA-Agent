"""The agent as a LangGraph workflow: plan, a bounded tool loop, code verification, and answer.

    plan ─┬─ ambiguous and interactive ─> clarify (interrupt) ─> plan
          └─> agent ⇄ tools ─(submit_answer)─> verify ─┬─ pass ─────────────> answer
                ^                                     ├─ fail, repairs left ─┘
                └─────────────────────────────────────└─ fail, none left ────> abstain

Routing and limits are code; the model supplies the plan, the queries, and the answer. A provider
failure leaves the graph without a new checkpoint, so the run stays open and a retry continues it.
State holds only plain data, so a checkpoint can be stored and a killed run resumed. Each model
turn is its own step, so a resume repeats at most the turn that was running. Tools only read, so
a repeated tool step is harmless. Run-time objects (the dataset, the model client) live in the
runtime context, never in state. See docs/adr/0013.
"""

import json
import operator
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
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
from da_agent.agent.planning import (
    PLAN_SPEC,
    SUBMIT_PLAN,
    parse_plan,
    plan_notes,
    plan_system_prompt,
    plan_user_prompt,
)
from da_agent.agent.prompts import (
    NUDGE,
    build_system_prompt,
    build_user_prompt,
    invalid_tool_call_notice,
    turns_left_notice,
)
from da_agent.agent.rules import RuleIndex
from da_agent.agent.tools import SUBMIT_ANSWER, Dataset, describe_tables, execute_tool, tool_specs
from da_agent.agent.verify import check_answer
from da_agent.llm.client import LLMClient
from da_agent.llm.types import (
    BudgetExceededError,
    InvalidToolCallError,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    ToolSpec,
)

MAX_CLARIFICATIONS = 1
REPAIR_NOTICE = (
    "Your answer failed verification: {problems}. Fix it with the queries it needs, then call "
    "submit_answer again. If the data cannot answer the question, submit kind 'unanswerable' "
    "with the reason."
)


class AgentState(TypedDict, total=False):
    question: str
    as_of: str
    clarifications: Annotated[list[str], operator.add]
    plan_steps: list[str]
    clarify_question: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    steps: Annotated[list[dict[str, Any]], operator.add]
    iterations: int
    nudges: int
    repairs: int
    proposed: dict[str, Any] | None
    verified: bool
    answer: dict[str, Any] | None
    stop_reason: str | None
    error: str | None


@dataclass
class AgentContext:
    run_id: str
    dataset: Dataset
    documents: list[Document]
    llm: LLMClient
    limits: AgentLimits
    interactive: bool
    tracer: Tracer
    rules: RuleIndex
    tools: list[ToolSpec]


Node = Callable[[AgentState, Runtime[AgentContext]], dict[str, Any]]


def _traced(name: str, node: Node) -> Node:
    def wrapper(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
        with runtime.context.tracer.start_as_current_span(f"node.{name}") as span:
            span.set_attribute("openinference.span.kind", "CHAIN")
            span.set_attribute("da_agent.run_id", runtime.context.run_id)
            return node(state, runtime)

    return wrapper


def _complete(context: AgentContext, request: LLMRequest) -> LLMResponse:
    return context.llm.complete(request, run_id=context.run_id, budget=context.limits.budget)


def _stop(reason: str, error: str | None = None) -> dict[str, Any]:
    return {"stop_reason": reason, "error": error}


# Nodes


def plan_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    context = runtime.context
    clarifications = state.get("clarifications", [])
    request = LLMRequest(
        messages=[
            Message(
                role="system", content=plan_system_prompt(context.documents, context.interactive)
            ),
            Message(
                role="user",
                content=plan_user_prompt(
                    state["question"], state["as_of"], describe_tables(context.dataset),
                    clarifications,
                ),
            ),
        ],
        tools=[PLAN_SPEC],
    )  # fmt: skip
    try:
        response = _complete(context, request)
    except BudgetExceededError:
        return _stop("budget_exceeded")
    except InvalidToolCallError:
        return {"plan_steps": [], "clarify_question": ""}
    call = next((c for c in response.tool_calls if c.name == SUBMIT_PLAN), None)
    plan = parse_plan(call.arguments) if call is not None and not call.arguments_error else None
    if plan is None:
        return {"plan_steps": [], "clarify_question": ""}
    asked = len(clarifications) >= MAX_CLARIFICATIONS
    question = plan.clarify_question if plan.ambiguous and not asked else ""
    return {"plan_steps": plan.steps, "clarify_question": question}


def clarify_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    reply = interrupt({"question": state.get("clarify_question", "")})
    return {"clarifications": [str(reply)], "clarify_question": ""}


def _first_messages(state: AgentState, context: AgentContext) -> list[Message]:
    question = build_user_prompt(state["question"], state["as_of"])
    notes = plan_notes(state.get("plan_steps", []), state.get("clarifications", []))
    tables = json.dumps(describe_tables(context.dataset), ensure_ascii=False)
    user = "\n\n".join(part for part in (question, notes, f"Dataset tables:\n{tables}") if part)
    return [
        Message(role="system", content=build_system_prompt(context.documents, schema_given=True)),
        Message(role="user", content=user),
    ]


def agent_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """One model turn. Limits, nudges, and malformed calls follow the minimal loop's rules."""
    context, limits = runtime.context, runtime.context.limits
    added: list[Message] = [] if state.get("messages") else _first_messages(state, context)
    iteration = state.get("iterations", 0) + 1
    turns_left = limits.max_iterations - iteration + 1
    notice = turns_left_notice(turns_left) if iteration > 1 else None
    if notice:
        added.append(Message(role="user", content=notice))
    # On the last turn only an answer can still count, so offer nothing else.
    tools = (
        [t for t in context.tools if t.name == SUBMIT_ANSWER] if turns_left == 1 else context.tools
    )
    history = [Message.model_validate(m) for m in state.get("messages", [])] + added
    update: dict[str, Any] = {"iterations": iteration}
    nudges = state.get("nudges", 0)
    try:
        response = _complete(context, LLMRequest(messages=history, tools=tools))
    except BudgetExceededError:
        return {**update, "iterations": iteration - 1, **_stop("budget_exceeded")}
    except InvalidToolCallError as error:
        # Each malformed call costs a turn, so the turn limit bounds them; the nudge limit is for
        # a model that stops calling tools at all.
        if turns_left == 1:
            update["messages"] = [m.model_dump() for m in added]
            return {**update, **_stop("invalid_tool_call", str(error))}
        added.append(
            Message(role="user", content=invalid_tool_call_notice([t.name for t in tools]))
        )
    else:
        added.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )
        if not response.tool_calls:
            if nudges >= limits.max_nudges:
                update.update(_stop("no_tool_call"))
            else:
                added.append(Message(role="user", content=NUDGE))
                update["nudges"] = nudges + 1
    if turns_left == 1 and not update.get("stop_reason") and not added[-1].tool_calls:
        update.update(_stop("iteration_limit"))
    update["messages"] = [m.model_dump() for m in added]
    return update


def tools_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Run the calls of the last model turn. Every tool only reads, so a resume may repeat this."""
    context = runtime.context
    last = Message.model_validate(state["messages"][-1])
    replies: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    proposed: dict[str, Any] | None = None
    for call in last.tool_calls:
        with context.tracer.start_as_current_span(f"tool.{call.name}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            if call.name == SUBMIT_ANSWER and not call.arguments_error:
                try:
                    answer = parse_answer(call.arguments)
                    proposed = proposed or asdict(answer)
                    content, step = '{"status": "received"}', ToolStep(tool=call.name, ok=True)
                except InvalidAnswerError as error:
                    content = json.dumps({"error": str(error)}, ensure_ascii=False)
                    step = ToolStep(tool=call.name, ok=False, error=str(error))
            else:
                content, step = execute_tool(call, context.dataset, context.rules)
            if step.sql:
                span.set_attribute("da_agent.sql", step.sql)
            span.set_attribute("da_agent.tool_ok", step.ok)
        steps.append(asdict(step))
        replies.append(
            Message(
                role="tool", content=content, tool_call_id=call.id, tool_name=call.name
            ).model_dump()
        )
    update: dict[str, Any] = {"messages": replies, "steps": steps, "proposed": proposed}
    if proposed is None and state.get("iterations", 0) >= context.limits.max_iterations:
        update.update(_stop("iteration_limit"))
    return update


def verify_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    limits = runtime.context.limits
    answer = AgentAnswer(**(state.get("proposed") or {}))
    messages = [Message.model_validate(m) for m in state.get("messages", [])]
    queries_ok = sum(1 for s in state.get("steps", []) if s["tool"] == "run_sql" and s["ok"])
    problems = check_answer(answer, messages, queries_ok)
    if not problems:
        return {"verified": True}
    repairs = state.get("repairs", 0)
    turns_left = limits.max_iterations - state.get("iterations", 0)
    if repairs >= limits.max_repairs or turns_left < 2:
        return {"verified": False, **_stop("unverified", "; ".join(problems))}
    notice = REPAIR_NOTICE.format(problems="; ".join(problems))
    return {
        "verified": False,
        "repairs": repairs + 1,
        "proposed": None,
        "messages": [Message(role="user", content=notice).model_dump()],
    }


def answer_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    return {"answer": state.get("proposed"), "stop_reason": "answered", "error": None}


def abstain_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    return {"answer": None, "stop_reason": state.get("stop_reason") or "abstained"}


# Routing


def _after_plan(state: AgentState, runtime: Runtime[AgentContext]) -> str:
    if state.get("stop_reason"):
        return "abstain"
    if runtime.context.interactive and state.get("clarify_question"):
        return "clarify"
    return "agent"


def _after_agent(state: AgentState) -> str:
    if state.get("stop_reason"):
        return "abstain"
    last = state["messages"][-1]
    return "tools" if last.get("tool_calls") else "agent"


def _after_tools(state: AgentState) -> str:
    if state.get("proposed") is not None:
        return "verify"
    return "abstain" if state.get("stop_reason") else "agent"


def _after_verify(state: AgentState) -> str:
    if state.get("verified"):
        return "answer"
    return "abstain" if state.get("stop_reason") else "agent"


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    nodes: dict[str, Node] = {
        "plan": plan_node,
        "clarify": clarify_node,
        "agent": agent_node,
        "tools": tools_node,
        "verify": verify_node,
        "answer": answer_node,
        "abstain": abstain_node,
    }
    for name, node in nodes.items():
        graph.add_node(name, _traced(name, node))
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", _after_plan, ["clarify", "agent", "abstain"])
    graph.add_edge("clarify", "plan")
    graph.add_conditional_edges("agent", _after_agent, ["tools", "agent", "abstain"])
    graph.add_conditional_edges("tools", _after_tools, ["verify", "agent", "abstain"])
    graph.add_conditional_edges("verify", _after_verify, ["answer", "agent", "abstain"])
    graph.add_edge("answer", END)
    graph.add_edge("abstain", END)
    return graph.compile(checkpointer=checkpointer)


# Entry point


def _result(values: dict[str, Any], question_for_user: str | None = None) -> AgentResult:
    answer = values.get("answer")
    return AgentResult(
        answer=AgentAnswer(**answer) if answer else None,
        stop_reason="needs_clarification"
        if question_for_user
        else values.get("stop_reason") or "abstained",
        steps=[ToolStep(**step) for step in values.get("steps", [])],
        iterations=values.get("iterations", 0),
        error=values.get("error"),
        question_for_user=question_for_user,
    )


def run_graph(
    question: str,
    *,
    as_of: str,
    dataset: Dataset,
    documents: list[Document],
    llm: LLMClient,
    run_id: str,
    limits: AgentLimits | None = None,
    interactive: bool = False,
    checkpointer: BaseCheckpointSaver | None = None,
    reply: str | None = None,
    tracer: Tracer | None = None,
    thread_id: str | None = None,
) -> AgentResult:
    """Answer one question, or continue the run saved under run_id.

    With a checkpointer, a run that stopped part-way (a killed worker, a pause to ask the user)
    continues from its last finished step, and a finished run returns its saved result. reply
    answers the question an interactive run paused on. thread_id names the saved run, and
    defaults to run_id, which also keys the cost ledger.
    """
    limits = limits or AgentLimits()
    tracer = tracer or trace.get_tracer("da_agent.agent")
    rules = RuleIndex(documents)
    context = AgentContext(
        run_id=run_id,
        dataset=dataset,
        documents=documents,
        llm=llm,
        limits=limits,
        interactive=interactive,
        tracer=tracer,
        rules=rules,
        tools=tool_specs(rules, getattr(dataset, "dialect", "SQLite")),
    )
    app = build_graph(checkpointer)
    config = {"configurable": {"thread_id": thread_id or run_id}}
    saved = app.get_state(config) if checkpointer is not None else None
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("da_agent.run_id", run_id)
        if saved is not None and saved.values and not saved.next:
            values = dict(saved.values)
        else:
            if saved is not None and saved.next:
                command: Any = Command(resume=reply) if reply is not None else None
            else:
                command = {"question": question, "as_of": as_of}
            try:
                values = app.invoke(command, config, context=context)
            except LLMError as error:
                # Every route failed. The run keeps its last checkpoint, so a later call retries.
                current = app.get_state(config).values if checkpointer is not None else {}
                result = _result({**current, "stop_reason": "llm_error", "error": str(error)})
                span.set_attribute("da_agent.stop_reason", result.stop_reason)
                return result
        pending = values.get("__interrupt__") or []
        asked = pending[0].value.get("question") if pending else None
        result = _result(values, asked)
        span.set_attribute("da_agent.stop_reason", result.stop_reason)
        span.set_attribute("da_agent.iterations", result.iterations)
        return result
