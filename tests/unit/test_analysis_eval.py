"""The fixed-analysis eval (ADR 0051): a seeded synthetic shop with planted effects, and a scorer
that reads the analysis behind each answer. No model calls here."""

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord
from eval.analysis.generate import QUESTIONS, generate
from eval.analysis.score import score

COMMITTED = Path("eval/analysis/questions.jsonl")


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("analysis")
    generate(out)
    return out


def test_the_generator_is_deterministic(generated: Path, tmp_path: Path) -> None:
    generate(tmp_path)

    for name in ("shop/orders.csv", "questions.jsonl"):
        assert (tmp_path / name).read_bytes() == (generated / name).read_bytes()


def test_the_committed_questions_match_the_generator(generated: Path) -> None:
    assert COMMITTED.read_text(encoding="utf-8") == (generated / QUESTIONS).read_text("utf-8")


def test_the_october_drop_is_planted_in_q1(generated: Path) -> None:
    revenue: dict[tuple[str, str], float] = defaultdict(float)
    with (generated / "shop/orders.csv").open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if row["status"] == "completed" and row["order_date"][:7] in ("2026-09", "2026-10"):
                revenue[(row["order_date"][:7], row["branch"])] += float(row["amount"])

    def total(month: str) -> float:
        return sum(v for (m, _), v in revenue.items() if m == month)

    drop = total("2026-10") - total("2026-09")
    q1 = revenue[("2026-10", "Q1")] - revenue[("2026-09", "Q1")]
    assert drop < 0 and q1 / drop > 0.6


def test_the_question_set_has_analyses_and_controls(generated: Path) -> None:
    questions = [json.loads(line) for line in (generated / QUESTIONS).read_text().splitlines()]

    kinds = [q["analysis"] for q in questions]
    assert kinds.count("none") == 6 and len(questions) == 16
    for kind in ("explain_change", "trend", "forecast", "correlation", "outliers"):
        assert kind in kinds
    assert all(q["as_of"] == "2026-11-26" and q["checks"] for q in questions)


# The scorer

CHANGE_SUMMARY = {
    "top": {"dimension": "branch", "segment": "Q1", "change": -900.0, "share_percent": 90.0},
    "change": -1000.0,
}


def analysis(kind: str, summary: dict[str, Any], rows: list[list[Any]] | None = None,
             arguments: dict[str, Any] | None = None) -> QueryRecord:  # fmt: skip
    return QueryRecord(
        sql="SELECT 1",
        columns=["a", "b", "c", "d", "e"][: len((rows or [[0]])[0])],
        rows=rows or [],
        analysis={"kind": kind, "arguments": arguments or {}, "summary": summary, "notes": []},
    )


def result(answer: AgentAnswer | None, *queries: QueryRecord) -> AgentResult:
    return AgentResult(answer=answer, queries=list(queries))


def question(kind: str, *checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "q",
        "question": "?",
        "as_of": "2026-11-26",
        "analysis": kind,
        "checks": list(checks),
    }


def test_top_segment_passes_on_the_evidence_analysis() -> None:
    asked = question(
        "explain_change", {"type": "top_segment", "dimension": "branch", "segment": "Q1"}
    )

    graded = score(
        asked, result(AgentAnswer("number", -1000.0), analysis("explain_change", CHANGE_SUMMARY))
    )

    assert (graded.kind_ok, graded.outcome, graded.failed) == (True, "correct", [])


def test_a_wrong_top_segment_is_wrong() -> None:
    asked = question(
        "explain_change", {"type": "top_segment", "dimension": "branch", "segment": "Q3"}
    )

    graded = score(
        asked, result(AgentAnswer("number", -1000.0), analysis("explain_change", CHANGE_SUMMARY))
    )

    assert (graded.outcome, graded.failed) == ("wrong", ["top_segment"])


def test_using_analysis_on_a_control_is_kind_wrong() -> None:
    asked = question("none", {"type": "number", "value": 350, "tolerance": 0.01})

    graded = score(
        asked, result(AgentAnswer("number", 350), analysis("trend", {"direction": "increasing"}))
    )

    assert graded.kind_ok is False and graded.outcome == "correct"


def test_an_analysis_question_answered_without_one_is_kind_wrong() -> None:
    asked = question("trend", {"type": "direction", "value": "increasing"})

    graded = score(asked, result(AgentAnswer("text", "rising")))

    assert (graded.kind_ok, graded.outcome) == (False, "wrong")


def test_an_unanswerable_answer_is_abstained() -> None:
    asked = question("trend", {"type": "direction", "value": "increasing"})

    assert score(asked, result(AgentAnswer("unanswerable"))).outcome == "abstained"
    assert score(asked, result(None)).outcome == "abstained"


def test_ranges_must_match_the_same_days() -> None:
    same_days = {"before": {"start": "2026-10-01", "end": "2026-10-26"},
                 "after": {"start": "2026-11-01", "end": "2026-11-26"}}  # fmt: skip
    asked = question("explain_change", {"type": "ranges", **same_days})
    whole = {"before": {"start": "2026-10-01", "end": "2026-10-31"},
             "after": {"start": "2026-11-01", "end": "2026-11-26"}}  # fmt: skip

    right = score(
        asked, result(AgentAnswer("number", 1), analysis("explain_change", {}, arguments=same_days))
    )
    wrong = score(
        asked, result(AgentAnswer("number", 1), analysis("explain_change", {}, arguments=whole))
    )

    assert (right.outcome, wrong.outcome) == ("correct", "wrong")


def test_a_number_check_uses_relative_tolerance() -> None:
    asked = question("none", {"type": "number", "value": 1000.0, "tolerance": 0.01})

    assert score(asked, result(AgentAnswer("number", 1009.0))).outcome == "correct"
    assert score(asked, result(AgentAnswer("number", 1020.0))).outcome == "wrong"


def test_forecast_near_reads_the_first_forecast_row() -> None:
    rows = [["2026-10-01", 900.0, None, None, None], ["2026-12-01", None, 1100.0, 900.0, 1300.0]]
    asked = question("forecast", {"type": "forecast_near", "value": 1000.0, "tolerance": 0.2})

    graded = score(asked, result(AgentAnswer("number", 1100.0), analysis("forecast", {}, rows)))

    assert graded.outcome == "correct"


def test_contains_reads_the_outlier_days() -> None:
    rows = [["2025-03-14", 900.0, 9.1, "high"], ["2025-08-09", 880.0, 8.7, "high"]]
    asked = question("outliers", {"type": "contains", "values": ["2025-03-14", "2025-08-09"]})

    graded = score(
        asked, result(AgentAnswer("list", ["2025-03-14"]), analysis("outliers", {}, rows))
    )

    assert graded.outcome == "correct"


def test_correlation_checks_sign_and_strength() -> None:
    asked = question(
        "correlation", {"type": "correlation", "sign": "positive", "min_strength": "moderate"}
    )

    strong = analysis("correlation", {"pearson": 0.7, "strength": "strong"})
    weak = analysis("correlation", {"pearson": 0.2, "strength": "weak"})

    assert score(asked, result(AgentAnswer("number", 0.7), strong)).outcome == "correct"
    assert score(asked, result(AgentAnswer("number", 0.2), weak)).outcome == "wrong"


def test_text_contains_reads_any_answer_shape() -> None:
    asked = question("none", {"type": "text_contains", "value": "GrabFood"})
    table = {"columns": ["channel", "revenue"], "rows": [["GrabFood", 1]]}

    assert score(asked, result(AgentAnswer("text", "Kênh grabfood"))).outcome == "correct"
    assert score(asked, result(AgentAnswer("table", table))).outcome == "correct"


def test_the_summary_counts_kinds_outcomes_and_pass_in_every_attempt() -> None:
    from eval.analysis.run import summarize

    def row(qid: str, kind: str, attempt: int, kind_ok: bool, outcome: str) -> dict[str, Any]:
        return {
            "id": qid, "analysis": kind, "attempt": attempt, "kind_ok": kind_ok,
            "outcome": outcome, "tool_errors": ["repeat"] if not kind_ok else [],
            "verify_log": ["passed"], "seconds": 2.0, "tokens": 100, "cost_usd": 0.001,
        }  # fmt: skip

    rows = [
        row("a", "trend", 1, True, "correct"), row("c", "none", 1, True, "correct"),
        row("a", "trend", 2, False, "wrong"), row("c", "none", 2, True, "correct"),
    ]  # fmt: skip

    summary = summarize(rows)

    first, second = summary["attempts"]
    assert (first["kind_right"], first["correct"], first["controls_without_analysis"]) == (1, 1, 1)
    assert (second["kind_right"], second["correct"]) == (0, 0)
    assert summary["pass_all_attempts"] == 1 and summary["questions"] == 2
    assert summary["tool_errors"] == {"repeat": 1}
    assert summary["cost_usd"] == 0.004
