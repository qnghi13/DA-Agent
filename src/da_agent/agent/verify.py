"""Code checks on a proposed answer. See docs/adr/0007.

Checks here are cheap and deterministic. A failed check goes back to the tool loop as a repair
request, and an answer that still fails is withheld, because a wrong number costs more than none.
Checks on periods, joins, and recomputation come later, in the reliability phase.
"""

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from da_agent.agent.answer import AgentAnswer
from da_agent.llm.types import Message

RUN_SQL = "run_sql"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _walk_numbers(value: Any) -> list[float]:
    if _is_number(value):
        return [float(value)]
    if isinstance(value, list):
        return [n for item in value for n in _walk_numbers(item)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in _walk_numbers(item)]
    return []


def result_numbers(messages: list[Message]) -> list[float]:
    """Every number in a successful query result the model has seen."""
    numbers: list[float] = []
    for message in messages:
        if message.role != "tool" or message.tool_name != RUN_SQL or not message.content:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "rows" in payload:
            numbers += _walk_numbers(payload["rows"])
    return numbers


def answer_numbers(answer: AgentAnswer) -> list[float]:
    if answer.kind in ("number", "percent", "list"):
        return _walk_numbers(answer.value)
    if answer.kind == "table" and isinstance(answer.value, dict):
        return _walk_numbers(answer.value.get("rows", []))
    return []


def _tolerance(value: float) -> float:
    """Half a unit in the last decimal place written, so a rounded result still matches."""
    try:
        exponent = Decimal(repr(value)).normalize().as_tuple().exponent
    except InvalidOperation:
        return 1e-9
    decimals = max(-exponent, 0) if isinstance(exponent, int) else 0
    return 0.5 * 10.0**-decimals


def _traced(value: float, numbers: list[float], percent: bool) -> bool:
    tolerance = _tolerance(value)
    candidates = numbers + ([n * 100 for n in numbers] if percent else [])
    return any(abs(value - n) <= tolerance for n in candidates)


def _show(value: float) -> str:
    return str(int(value)) if value.is_integer() else repr(value)


def check_answer(answer: AgentAnswer, messages: list[Message], queries_ok: int) -> list[str]:
    """Problems with a proposed answer, written for the model. Empty when it passes."""
    if queries_ok == 0:
        return ["no query succeeded, so the answer has no evidence; run the queries it needs"]
    numbers = result_numbers(messages)
    untraced = [
        value
        for value in answer_numbers(answer)
        if not _traced(value, numbers, percent=answer.kind == "percent")
    ]
    if untraced:
        shown = ", ".join(_show(value) for value in untraced[:5])
        return [
            f"the numbers {shown} do not appear in any query result; compute each number in SQL "
            "and answer with the value the query returns"
        ]
    return []
