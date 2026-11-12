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
from da_agent.agent.charts import MAX_CHARTS
from da_agent.agent.data_checks import fanout_problems, unreadable_problems
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
from da_agent.agent.verify import (
    DateRanges,
    Problem,
    answer_problems,
    date_ranges,
    evidence_queries,
    query_records,
)
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
    # The problems verify found in the last answer, for progress events.
    verify_problems: list[str]
    # Each verify pass adds the problem kinds it found, or "passed". Kinds only, so traces,
    # events, and eval rows can count them without carrying the user's data.
    verify_log: Annotated[list[str], operator.add]
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
    # The period each date column covers, for the period check in verify.
    ranges: DateRanges
    dialect: str
    # Whether make_chart is offered.
    charts: bool = False


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
                    step = ToolStep(
                        tool=call.name, ok=False, error=str(error), error_kind="answer_format"
                    )
            else:
                content, step = execute_tool(
                    call,
                    context.dataset,
                    context.rules,
                    charts_left=_charts_left(context, state, steps),
                )
            if step.sql:
                span.set_attribute("da_agent.sql", step.sql)
            span.set_attribute("da_agent.tool_ok", step.ok)
            if step.error_kind:
                span.set_attribute("da_agent.tool.error_kind", step.error_kind)
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


def _charts_left(
    context: AgentContext, state: AgentState, this_turn: list[dict[str, Any]]
) -> int | None:
    if not context.charts:
        return None
    drawn = sum(1 for step in [*state.get("steps", []), *this_turn] if step.get("chart"))
    return MAX_CHARTS - drawn


def verify_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    limits = runtime.context.limits
    answer = AgentAnswer(**(state.get("proposed") or {}))
    messages = [Message.model_validate(m) for m in state.get("messages", [])]
    queries_ok = sum(1 for s in state.get("steps", []) if s["tool"] == "run_sql" and s["ok"])
    context = runtime.context
    found = answer_problems(answer, messages, queries_ok, context.ranges, context.dialect)
    span = trace.get_current_span()
    if not found and answer.kind != "unanswerable":
        # Probes run read-only queries in the sandbox, so they come after the free checks.
        evidence = evidence_queries(answer, messages)
        found = [
            Problem("fanout", m)
            for m in fanout_problems(evidence, context.dataset, context.dialect)
        ]
        if not found:
            # The model cannot repair values the build could not read, so this one only stops.
            unreadable = unreadable_problems(evidence, context.dataset, context.dialect)
            span.set_attribute("da_agent.verify.unreadable", bool(unreadable))
            if unreadable:
                span.set_attribute("da_agent.verify.kinds", ["unreadable"])
                return {
                    "verified": False,
                    "verify_problems": unreadable,
                    "verify_log": ["unreadable"],
                    **_stop("unreadable_values", "; ".join(unreadable)),
                }
    problems = [p.message for p in found]
    kinds = sorted({p.kind for p in found})
    span.set_attribute("da_agent.verify.problems", len(problems))
    if not problems:
        return {"verified": True, "verify_problems": [], "verify_log": ["passed"]}
    span.set_attribute("da_agent.verify.kinds", kinds)
    repairs = state.get("repairs", 0)
    turns_left = limits.max_iterations - state.get("iterations", 0)
    if repairs >= limits.max_repairs or turns_left < 2:
        return {
            "verified": False,
            "verify_problems": problems,
            "verify_log": kinds,
            **_stop("unverified", "; ".join(problems)),
        }
    notice = REPAIR_NOTICE.format(problems="; ".join(problems))
    return {
        "verified": False,
        "verify_problems": problems,
        "verify_log": kinds,
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


# Progress events


EventSink = Callable[[dict[str, Any]], None]


def node_events(node: str, update: dict[str, Any]) -> list[dict[str, Any]]:
    """Small progress events for one node's state update. They name tools, queries, and verdicts,
    and never carry query rows or message text."""
    if node == "plan":
        return [{"type": "plan", "steps": update.get("plan_steps", [])}]
    if node == "agent":
        calls = [
            call["name"]
            for message in update.get("messages", [])
            if message.get("role") == "assistant"
            for call in message.get("tool_calls") or []
        ]
        return [{"type": "turn", "iteration": update.get("iterations"), "tools": calls}]
    if node == "tools":
        return [
            {
                "type": "tool",
                "tool": step["tool"],
                "ok": step["ok"],
                "sql": step.get("sql"),
                "row_count": step.get("row_count"),
                "error": step.get("error"),
                "chart": step.get("chart") is not None,
                "error_kind": step.get("error_kind"),
            }
            for step in update.get("steps", [])
        ]
    if node == "verify":
        passed = bool(update.get("verified"))
        checks = [kind for kind in update.get("verify_log", []) if kind != "passed"]
        problems = update.get("verify_problems", [])
        return [{"type": "verify", "passed": passed, "problems": problems, "checks": checks}]
    if node in ("answer", "abstain"):
        return [{"type": "done", "stop_reason": update.get("stop_reason")}]
    return []


def _emit(sink: EventSink | None, events: list[dict[str, Any]]) -> None:
    if sink is None:
        return
    for event in events:
        try:
            sink(event)
        except Exception as error:  # Progress is best effort: the run must not fail over it.
            trace.get_current_span().record_exception(error)


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
        queries=query_records([Message.model_validate(m) for m in values.get("messages", [])]),
        verify_log=list(values.get("verify_log", [])),
    )


def _stream(
    app: Any, command: Any, config: dict[str, Any], context: AgentContext, sink: EventSink | None
) -> tuple[dict[str, Any], str | None]:
    """Run the graph, sending progress events. Returns the final state and the question an
    interactive run paused on, if any."""
    values: dict[str, Any] = {}
    asked: str | None = None
    for mode, chunk in app.stream(
        command, config, context=context, stream_mode=["updates", "values"]
    ):
        if mode == "values":
            values = chunk
            continue
        for node, update in chunk.items():
            if node == "__interrupt__":
                asked = update[0].value.get("question") if update else None
                _emit(sink, [{"type": "question", "question": asked}])
            elif isinstance(update, dict):
                _emit(sink, node_events(node, update))
    return values, asked


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
    charts: bool = False,
    on_event: EventSink | None = None,
) -> AgentResult:
    """Answer one question, or continue the run saved under run_id.

    With a checkpointer, a run that stopped part-way (a killed worker, a pause to ask the user)
    continues from its last finished step, and a finished run returns its saved result. reply
    answers the question an interactive run paused on. thread_id names the saved run, and
    defaults to run_id, which also keys the cost ledger. charts offers make_chart, whose specs
    come back in AgentResult.charts. on_event receives progress events (see node_events) as the
    run goes; a resumed run may repeat the events of the step that was running.
    """
    limits = limits or AgentLimits()
    tracer = tracer or trace.get_tracer("da_agent.agent")
    rules = RuleIndex(documents)
    dialect = getattr(dataset, "dialect", "SQLite")
    context = AgentContext(
        run_id=run_id,
        dataset=dataset,
        documents=documents,
        llm=llm,
        limits=limits,
        interactive=interactive,
        tracer=tracer,
        rules=rules,
        tools=tool_specs(rules, dialect, charts),
        ranges=date_ranges(
            [(c.name, c.value_range) for table in dataset.describe() for c in table.columns]
        ),
        dialect=dialect.lower(),
        charts=charts,
    )
    app = build_graph(checkpointer)
    config = {"configurable": {"thread_id": thread_id or run_id}}
    saved = app.get_state(config) if checkpointer is not None else None
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("da_agent.run_id", run_id)
        if saved is not None and saved.values and not saved.next:
            values, asked = dict(saved.values), None
        else:
            if saved is not None and saved.next:
                command: Any = Command(resume=reply) if reply is not None else None
            else:
                command = {"question": question, "as_of": as_of}
            try:
                values, asked = _stream(app, command, config, context, on_event)
            except LLMError as error:
                # Every route failed. The run keeps its last checkpoint, so a later call retries.
                current = app.get_state(config).values if checkpointer is not None else {}
                result = _result({**current, "stop_reason": "llm_error", "error": str(error)})
                span.set_attribute("da_agent.stop_reason", result.stop_reason)
                return result
        result = _result(values, asked)
        span.set_attribute("da_agent.stop_reason", result.stop_reason)
        span.set_attribute("da_agent.iterations", result.iterations)
        return result
