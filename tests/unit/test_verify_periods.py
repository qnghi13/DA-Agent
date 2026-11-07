"""The period check: an answer computed over a period the data does not cover is not an answer."""

import datetime as dt
import json

import pytest

from da_agent.agent.answer import AgentAnswer
from da_agent.agent.verify import Period, check_answer, query_periods
from da_agent.llm.types import Message, ToolCall

D = dt.date
RANGES = {"ngaylap": [("2025-01-01", "2026-06-28")]}


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("NgayLap BETWEEN '2025-03-01' AND '2025-03-31'", (D(2025, 3, 1), D(2025, 3, 31))),
        (
            "d.NgayLap >= DATE '2025-03-01' AND d.NgayLap < '2025-04-01'",
            (D(2025, 3, 1), D(2025, 3, 31)),
        ),
        (
            "NgayLap >= '2025-03-01' AND NgayLap <= '2025-03-31 23:59:59'",
            (D(2025, 3, 1), D(2025, 3, 31)),
        ),
        ("NgayLap = '2025-03-05'", (D(2025, 3, 5), D(2025, 3, 5))),
        ("CAST(NgayLap AS DATE) = DATE '2025-03-05'", (D(2025, 3, 5), D(2025, 3, 5))),
        ("strftime(NgayLap, '%Y-%m') = '2025-02'", (D(2025, 2, 1), D(2025, 2, 28))),
        ("strftime('%Y-%m', NgayLap) = '2024-02'", (D(2024, 2, 1), D(2024, 2, 29))),
        ("strftime(NgayLap, '%Y') = '2025'", (D(2025, 1, 1), D(2025, 12, 31))),
        ("date_trunc('month', NgayLap) = DATE '2025-06-01'", (D(2025, 6, 1), D(2025, 6, 30))),
        ("NgayLap >= '2025-03-01'", (D(2025, 3, 1), None)),
    ],
)
def test_periods_are_read_from_date_filters(where: str, expected: tuple) -> None:
    sql = f"SELECT count(*) FROM don_hang d WHERE {where}"  # noqa: S608 - test input, never run
    [period] = query_periods(sql, "duckdb")

    assert period.column == "ngaylap"
    assert (period.start, period.end) == expected


def test_filters_that_are_not_dates_are_ignored() -> None:
    sql = "SELECT 1 FROM t WHERE status = 'done' AND total >= 100 AND note LIKE '2025%'"

    assert query_periods(sql, "duckdb") == []


def test_separate_conditions_give_separate_periods() -> None:
    sql = (
        "SELECT sum(CASE WHEN NgayLap BETWEEN '2025-10-01' AND '2025-10-31' THEN 1 END), "
        "sum(CASE WHEN NgayLap BETWEEN '2025-11-01' AND '2025-11-30' THEN 1 END) FROM t"
    )

    periods = query_periods(sql, "duckdb")

    assert sorted((p.start, p.end) for p in periods) == [
        (D(2025, 10, 1), D(2025, 10, 31)),
        (D(2025, 11, 1), D(2025, 11, 30)),
    ]


def test_unparsable_sql_gives_no_periods() -> None:
    assert query_periods("SELEC nonsense ((", "duckdb") == []


def conversation(*queries: tuple[str, list]) -> list[Message]:
    """A run where each query returned the given rows."""
    messages = [Message(role="user", content="q")]
    for n, (sql, rows) in enumerate(queries):
        call = ToolCall(id=f"c{n}", name="run_sql", arguments={"sql": sql})
        messages.append(Message(role="assistant", tool_calls=[call]))
        content = json.dumps({"columns": ["v"], "rows": rows, "truncated": False})
        messages.append(
            Message(role="tool", content=content, tool_call_id=f"c{n}", tool_name="run_sql")
        )
    return messages


AUGUST = "SELECT sum(total) FROM don_hang WHERE NgayLap BETWEEN '2026-08-01' AND '2026-08-31'"
JUNE = "SELECT sum(total) FROM don_hang WHERE NgayLap BETWEEN '2026-06-01' AND '2026-06-30'"


def test_an_answer_from_a_period_with_no_data_is_rejected() -> None:
    problems = check_answer(
        AgentAnswer("number", 0), conversation((AUGUST, [[0]])), queries_ok=1, ranges=RANGES
    )

    assert len(problems) == 1
    assert "2026-08-01" in problems[0] and "2026-06-28" in problems[0]
    assert "unanswerable" in problems[0]


def test_a_period_that_overlaps_the_data_passes() -> None:
    problems = check_answer(
        AgentAnswer("number", 500), conversation((JUNE, [[500]])), queries_ok=1, ranges=RANGES
    )

    assert problems == []


def test_only_the_queries_behind_the_answer_are_checked() -> None:
    """A probe outside the data does not condemn an answer taken from another query."""
    messages = conversation((AUGUST, [[None]]), (JUNE, [[500]]))

    assert check_answer(AgentAnswer("number", 500), messages, queries_ok=2, ranges=RANGES) == []


def test_text_answers_are_checked_against_the_last_query() -> None:
    messages = conversation((JUNE, [["An"]]), (AUGUST, [["Binh"]]))

    problems = check_answer(AgentAnswer("text", "Binh"), messages, queries_ok=2, ranges=RANGES)

    assert len(problems) == 1


def test_unanswerable_is_not_period_checked() -> None:
    messages = conversation((AUGUST, [[0]]))

    assert check_answer(AgentAnswer("unanswerable"), messages, queries_ok=1, ranges=RANGES) == []


def test_columns_without_a_known_range_are_not_checked() -> None:
    messages = conversation((AUGUST, [[0]]))

    assert check_answer(AgentAnswer("number", 0), messages, queries_ok=1, ranges={}) == []


def test_period_is_a_plain_value() -> None:
    assert Period("ngaylap", D(2025, 1, 1), None).end is None
