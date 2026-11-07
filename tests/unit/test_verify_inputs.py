"""Arithmetic on numbers typed into SQL is fine when those numbers came from the data. A query that
reads no table proves nothing about numbers the model invented."""

import json

from da_agent.agent.answer import AgentAnswer
from da_agent.agent.verify import check_answer
from da_agent.llm.types import Message, ToolCall


def conversation(*queries: tuple[str, list]) -> list[Message]:
    messages = [Message(role="user", content="q")]
    for n, (sql, rows) in enumerate(queries):
        call = ToolCall(id=f"c{n}", name="run_sql", arguments={"sql": sql})
        messages.append(Message(role="assistant", tool_calls=[call]))
        content = json.dumps({"columns": ["v"], "rows": rows, "truncated": False})
        messages.append(
            Message(role="tool", content=content, tool_call_id=f"c{n}", tool_name="run_sql")
        )
    return messages


COUNTS = ("SELECT count(*) FILTER (WHERE late), count(*) FROM orders", [[11, 913]])


def test_arithmetic_on_numbers_from_the_data_passes() -> None:
    ratio = ("SELECT ROUND(11.0 / 913 * 100, 2) AS pct", [[1.2]])

    problems = check_answer(AgentAnswer("percent", 1.2), conversation(COUNTS, ratio), queries_ok=2)

    assert problems == []


def test_arithmetic_on_invented_numbers_is_rejected() -> None:
    ratio = ("SELECT ROUND(12.0 / 913 * 100, 2) AS pct", [[1.31]])

    problems = check_answer(AgentAnswer("percent", 1.31), conversation(COUNTS, ratio), queries_ok=2)

    assert len(problems) == 1
    assert "12" in problems[0]


def test_a_query_without_any_data_query_is_rejected() -> None:
    made_up = ("SELECT 500 AS total", [[500]])

    problems = check_answer(AgentAnswer("number", 500), conversation(made_up), queries_ok=1)

    assert len(problems) == 1


def test_a_number_also_returned_by_a_data_query_passes() -> None:
    both = (COUNTS, ("SELECT 913 AS n", [[913]]))

    assert check_answer(AgentAnswer("number", 913), conversation(*both), queries_ok=2) == []
