"""Score one answer of the fixed-analysis eval (ADR 0051).

A question names the analysis it needs ("none" for a control) and checks on the answer or on the
analysis behind it. Code grades both: did the agent run the right kind, and do the numbers hold.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from da_agent.agent.answer import AgentResult, QueryRecord

STRENGTHS = ("none", "weak", "moderate", "strong")
Outcome = Literal["correct", "wrong", "abstained"]


@dataclass(frozen=True)
class Score:
    kind_ok: bool
    outcome: Outcome
    failed: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)


def _analysis(result: AgentResult, kind: str) -> QueryRecord | None:
    """The last analysis of that kind the run made."""
    found = [q for q in result.analyses if q.analysis and q.analysis.get("kind") == kind]
    return found[-1] if found else None


def _close(value: Any, expected: float, tolerance: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if expected == 0:
        return abs(value) <= tolerance
    return abs(value - expected) <= abs(expected) * tolerance


def _texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(c) for row in value.get("rows", []) for c in row] + list(
            map(str, value.get("columns", []))
        )
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _passes(check: dict[str, Any], result: AgentResult, kind: str) -> bool:
    answer = result.answer
    record = _analysis(result, kind)
    info = (record.analysis or {}) if record else {}
    summary = info.get("summary") or {}
    match check["type"]:
        case "number":
            return answer is not None and _close(answer.value, check["value"], check["tolerance"])
        case "text_contains":
            wanted = str(check["value"]).casefold()
            return answer is not None and any(wanted in t.casefold() for t in _texts(answer.value))
        case "top_segment":
            top = summary.get("top") or {}
            return (
                top.get("dimension") == check["dimension"]
                and top.get("segment") == check["segment"]
            )
        case "ranges":
            arguments = info.get("arguments") or {}
            return (arguments.get("before"), arguments.get("after")) == (
                check["before"],
                check["after"],
            )
        case "direction":
            return summary.get("direction") == check["value"]
        case "forecast_near":
            rows = record.rows if record else []
            ahead = [row[2] for row in rows if len(row) > 2 and row[1] is None]
            return bool(ahead) and _close(ahead[0], check["value"], check["tolerance"])
        case "correlation":
            pearson = summary.get("pearson")
            if not isinstance(pearson, int | float):
                return False
            sign = "positive" if pearson > 0 else "negative"
            strength = summary.get("strength")
            strong_enough = strength in STRENGTHS and STRENGTHS.index(strength) >= STRENGTHS.index(
                check["min_strength"]
            )
            return sign == check["sign"] and strong_enough
        case "contains":
            found = {str(row[0])[:10] for row in (record.rows if record else [])}
            return set(check["values"]) <= found
    raise ValueError(f"unknown check type {check['type']!r}")


def score(question: dict[str, Any], result: AgentResult) -> Score:
    kinds = [str(q.analysis.get("kind")) for q in result.analyses if q.analysis]
    expected = question["analysis"]
    kind_ok = not kinds if expected == "none" else expected in kinds
    answer = result.answer
    if answer is None or answer.kind == "unanswerable":
        return Score(kind_ok=kind_ok, outcome="abstained", kinds=kinds)
    failed = [c["type"] for c in question["checks"] if not _passes(c, result, expected)]
    return Score(kind_ok=kind_ok, outcome="wrong" if failed else "correct", failed=failed,
                 kinds=kinds)  # fmt: skip
