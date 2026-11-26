"""Analyses are evidence (ADR 0051): a successful analyze call is a query record, its numbers trace,
and the SQL checks, the report, and the plain explanation all read it."""

import json
from typing import Any

from da_agent.agent.answer import (
    AgentAnswer,
    AgentResult,
    QueryRecord,
    ToolStep,
    result_from_json,
    result_to_json,
)
from da_agent.agent.verify import (
    answer_problems,
    evidence_queries,
    query_records,
    record_numbers,
    result_numbers,
)
from da_agent.llm.types import Message, ToolCall
from da_agent.reports.explain import Labels, explain_result
from da_agent.reports.html import render_report

CHANGE_SQL = "SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL"
PROBE_SQL = "SELECT count(*) AS n FROM orders"
TABLE = {
    "columns": ["dimension", "segment", "before", "after", "change", "share_percent", "status"],
    "rows": [
        ["(total)", "(all)", 6000.0, 4650.0, -1350.0, 100.0, "both"],
        ["branch", "Q1", 3000.0, 1550.0, -1450.0, 107.4, "both"],
        ["branch", "Q3", 3000.0, 3100.0, 100.0, -7.4, "both"],
    ],
    "truncated": False,
}
SUMMARY = {"change": -1350.0, "change_percent": -22.5, "top": {"segment": "Q1"}}
ARGUMENTS = {"x": "day", "y": "revenue", "dimensions": ["branch"], "periods": 1}


def analysis_payload(**changes: Any) -> dict[str, Any]:
    payload = {
        "kind": "explain_change",
        "arguments": ARGUMENTS,
        "summary": SUMMARY,
        "table": TABLE,
        "notes": [{"code": "flat_total", "params": {}, "text": "t"}],
        "guidance": "These are where the change happened",
    }
    return payload | changes


def conversation(*calls: tuple[str, str, dict[str, Any]]) -> list[Message]:
    """One assistant turn per call, each followed by its tool reply."""
    messages: list[Message] = []
    for number, (tool, sql, payload) in enumerate(calls, start=1):
        call = ToolCall(id=f"c{number}", name=tool, arguments={"sql": sql})
        messages.append(Message(role="assistant", tool_calls=[call]))
        messages.append(
            Message(
                role="tool",
                content=json.dumps(payload),
                tool_call_id=call.id,
                tool_name=tool,
            )
        )
    return messages


def test_an_analysis_is_a_query_record_with_its_table_and_summary() -> None:
    [record] = query_records(conversation(("analyze", CHANGE_SQL, analysis_payload())))

    assert record.sql == CHANGE_SQL
    assert (record.columns, record.rows, record.truncated) == (
        TABLE["columns"],
        TABLE["rows"],
        False,
    )
    assert record.analysis == {
        "kind": "explain_change",
        "arguments": ARGUMENTS,
        "summary": SUMMARY,
        "notes": [{"code": "flat_total", "params": {}, "text": "t"}],
    }
    assert -22.5 in record_numbers(record) and -1450.0 in record_numbers(record)


def test_a_correlation_without_a_table_is_still_a_record() -> None:
    payload = analysis_payload(kind="correlation", table=None, summary={"pearson": 0.81})
    [record] = query_records(conversation(("analyze", "SELECT a, b FROM t", payload)))

    assert (record.columns, record.rows) == ([], [])
    assert record_numbers(record) == [0.81]


def test_a_number_from_an_analysis_summary_traces() -> None:
    messages = conversation(("analyze", CHANGE_SQL, analysis_payload()))

    assert -22.5 in result_numbers(messages)
    assert answer_problems(AgentAnswer("percent", -22.5), messages, queries_ok=1) == []


def test_the_analysis_is_the_answers_evidence() -> None:
    messages = conversation(
        ("run_sql", PROBE_SQL, {"columns": ["n"], "rows": [[122]], "truncated": False}),
        ("analyze", CHANGE_SQL, analysis_payload()),
    )

    assert evidence_queries(AgentAnswer("number", -1450.0), messages) == [CHANGE_SQL]


def test_the_period_check_reads_analysis_sql() -> None:
    sql = (
        "SELECT day, branch, SUM(amount) AS revenue FROM orders "
        "WHERE day BETWEEN '2030-01-01' AND '2030-01-31' GROUP BY ALL"
    )
    messages = conversation(("analyze", sql, analysis_payload()))
    ranges = {"day": [("2026-01-01", "2026-10-31")]}

    problems = answer_problems(AgentAnswer("number", -1350.0), messages, 1, ranges, "duckdb")

    assert [problem.kind for problem in problems] == ["period"]


def test_a_failed_analysis_is_not_evidence() -> None:
    messages = conversation(("analyze", CHANGE_SQL, {"error": "no rows between a and b"}))

    assert query_records(messages) == []
    assert result_numbers(messages) == []


def test_old_results_without_analysis_load() -> None:
    data = {
        "answer": {"kind": "number", "value": 350, "reason": None},
        "stop_reason": "answered",
        "steps": [{"tool": "run_sql", "ok": True, "sql": PROBE_SQL, "row_count": 1}],
        "queries": [{"sql": PROBE_SQL, "columns": ["n"], "rows": [[350]], "truncated": False}],
    }

    result = result_from_json(data)

    assert result.queries[0].analysis is None
    assert result.analyses == []


def test_report_and_explanation_cover_an_analysis() -> None:
    [record] = query_records(conversation(("analyze", CHANGE_SQL, analysis_payload())))
    result = AgentResult(
        answer=AgentAnswer("number", -1450.0),
        steps=[ToolStep(tool="analyze", ok=True, sql=CHANGE_SQL, row_count=3)],
        queries=[record],
    )

    assert result.analyses == [record]
    [explanation] = explain_result(result, Labels())
    assert explanation["query_index"] == 0
    html = render_report(result, question="Why did revenue drop?", as_of="2026-11-26")
    assert CHANGE_SQL in html
    assert 'href="#q1-r2-c5"' in html


def test_a_record_round_trips_through_json() -> None:
    record = QueryRecord(sql=CHANGE_SQL, columns=["a"], rows=[[1]], analysis={"kind": "trend"})
    result = AgentResult(queries=[record])

    assert result_from_json(result_to_json(result)).queries == [record]
