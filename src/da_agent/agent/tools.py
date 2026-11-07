"""Tools the minimal agent can call. Failures return as data the model can act on."""

import json
from typing import Any, Protocol

from opentelemetry import trace

from da_agent.agent.answer import ANSWER_KINDS, ToolStep
from da_agent.agent.charts import MAKE_CHART, MAKE_CHART_SPEC, MAX_CHARTS, ChartError, chart_spec
from da_agent.agent.rules import RuleIndex
from da_agent.llm.types import ToolCall, ToolSpec
from da_agent.storage.sqlite_dataset import Column, QueryError, QueryResult, TableInfo

SUBMIT_ANSWER = "submit_answer"
SEARCH_RULES = "search_rules"
# Bounds one search result, because it stays in the conversation for the rest of the run.
MAX_RULE_CHARS = 6000

TOOL_SPECS = [
    ToolSpec(
        name="describe_dataset",
        description="List the tables, their row counts, and their visible columns.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="run_sql",
        description=(
            "Run one read-only SQLite SELECT and get the result. Rows are limited, so aggregate "
            "in SQL instead of reading rows. On failure, the result explains the error."
        ),
        parameters={
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
            "required": ["sql"],
        },
    ),
    ToolSpec(
        name=SUBMIT_ANSWER,
        description=(
            "Submit the final answer and end the run. Use kind 'unanswerable' when the data "
            "cannot answer the question, and explain why in reason."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(ANSWER_KINDS)},
                "value_json": {
                    "type": "string",
                    "description": (
                        'The answer encoded as JSON text: 4820000, 12.4, "2026-08-15", true, '
                        '"Name", ["a", "b"], or '
                        '{"columns": ["a", "b"], "rows": [["x", 1]]}. '
                        "Omit it for kind 'unanswerable'."
                    ),
                },
                "reason": {"type": "string", "description": "A short explanation."},
            },
            "required": ["kind"],
        },
    ),
]


SEARCH_RULES_SPEC = ToolSpec(
    name=SEARCH_RULES,
    description=(
        "Fetch rule sections that are listed by title in the documents. Pass a section title, "
        "its number, or keywords. Returns at most two sections."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Section title or keywords."}},
        "required": ["query"],
    },
)


DIALECT_HINTS = {
    "DuckDB": (
        " To find text such as a product name, match case and accents loosely, for example "
        "strip_accents(lower(name)) LIKE '%vien sui%', or ILIKE."
    ),
}


def tool_specs(
    rules: RuleIndex | None, dialect: str = "SQLite", charts: bool = False
) -> list[ToolSpec]:
    """search_rules is offered only when there are sections to search, and make_chart only when
    the run asks for charts. run_sql names the SQL dialect of the dataset, so the model writes
    functions that exist."""
    hint = DIALECT_HINTS.get(dialect, "")
    specs = [
        spec.model_copy(update={"description": spec.description.replace("SQLite", dialect) + hint})
        if spec.name == "run_sql"
        else spec
        for spec in TOOL_SPECS
    ]
    if rules is not None and rules.sections:
        specs.append(SEARCH_RULES_SPEC)
    if charts:
        specs.append(MAKE_CHART_SPEC)
    return specs


class Dataset(Protocol):
    dialect: str

    def describe(self) -> list[TableInfo]: ...

    def query(self, sql: str) -> QueryResult: ...


def _describe_column(column: Column) -> str:
    text = f"{column.name}:{column.type}"
    if column.value_range is not None:
        text += f" {column.value_range[0]}..{column.value_range[1]}"
    if column.source and column.source != column.name:
        text += f" (source {column.source})"
    return text


def _describe_table(table: TableInfo) -> dict[str, Any]:
    # Compact on purpose, because this text is resent on every later turn of the run.
    described: dict[str, Any] = {"name": table.name}
    if table.description:
        described["row"] = table.description
    described |= {
        "rows": table.row_count,
        "columns": ", ".join(_describe_column(column) for column in table.columns),
    }
    values = {column.name: list(column.values) for column in table.columns if column.values}
    if values:
        described["values"] = values
    return described


def describe_tables(dataset: Dataset) -> list[dict[str, Any]]:
    """The compact schema that describe_dataset returns."""
    return [_describe_table(table) for table in dataset.describe()]


def _error(tool: str, message: str, sql: str | None = None) -> tuple[str, ToolStep]:
    return json.dumps({"error": message}, ensure_ascii=False), ToolStep(
        tool=tool, ok=False, sql=sql, error=message
    )


def execute_tool(
    call: ToolCall,
    dataset: Dataset,
    rules: RuleIndex | None = None,
    *,
    charts_left: int | None = None,
) -> tuple[str, ToolStep]:
    """Run a read-only tool. Returns the text for the model and a record of the step.

    charts_left is None when the run does not offer make_chart, and otherwise how many more
    charts the run may draw.

    A crash in a tool, such as a lost sandbox, becomes an error result the model can act on
    within its turn limit. The model sees only the error type: the message may hold host paths.
    The trace keeps the full exception.
    """
    try:
        return _execute(call, dataset, rules, charts_left)
    except Exception as error:  # Any crash becomes a tool error, by design.
        trace.get_current_span().record_exception(error)
        message = f"the tool failed unexpectedly ({type(error).__name__}); try again once"
        sql = call.arguments.get("sql") if call.name in ("run_sql", MAKE_CHART) else None
        return _error(call.name, message, sql=sql if isinstance(sql, str) else None)


def _execute(
    call: ToolCall, dataset: Dataset, rules: RuleIndex | None, charts_left: int | None
) -> tuple[str, ToolStep]:
    if call.arguments_error:
        return _error(call.name, f"could not read the tool arguments: {call.arguments_error}")
    if call.name == "describe_dataset":
        tables = describe_tables(dataset)
        return json.dumps({"tables": tables}, ensure_ascii=False), ToolStep(tool=call.name, ok=True)
    if call.name == "run_sql":
        return _run_sql_on(dataset, call.arguments)
    if call.name == SEARCH_RULES and rules is not None and rules.sections:
        return _search_rules(rules, call.arguments)
    if call.name == MAKE_CHART and charts_left is not None:
        return _make_chart(dataset, call.arguments, charts_left)
    known = [spec.name for spec in tool_specs(rules, charts=charts_left is not None)]
    return _error(call.name, f"unknown tool {call.name!r}, available tools: {known}")


def _run_sql_on(dataset: Dataset, arguments: dict[str, Any]) -> tuple[str, ToolStep]:
    sql = arguments.get("sql")
    if not isinstance(sql, str):
        return _error("run_sql", "sql is required and must be a string")
    try:
        result = dataset.query(sql)
    except QueryError as error:
        return _error("run_sql", str(error), sql=sql)
    payload = {"columns": result.columns, "rows": result.rows, "truncated": result.truncated}
    return json.dumps(payload, ensure_ascii=False, default=str), ToolStep(
        tool="run_sql", ok=True, sql=sql, row_count=len(result.rows)
    )


def _search_rules(rules: RuleIndex, arguments: dict[str, Any]) -> tuple[str, ToolStep]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _error(SEARCH_RULES, "query is required and must be a non-empty string")
    found = rules.search(query)
    if not found:
        message = f"no section matches {query!r}. Sections: {rules.titles()}"
        return _error(SEARCH_RULES, message)
    text = "\n\n".join(section.text for section in found)[:MAX_RULE_CHARS]
    payload = {"sections": [section.title for section in found], "text": text}
    return json.dumps(payload, ensure_ascii=False), ToolStep(tool=SEARCH_RULES, ok=True)


def _make_chart(
    dataset: Dataset, arguments: dict[str, Any], charts_left: int
) -> tuple[str, ToolStep]:
    sql = arguments.get("sql")
    if not isinstance(sql, str):
        return _error(MAKE_CHART, "sql is required and must be a string")
    if charts_left <= 0:
        return _error(MAKE_CHART, f"a run draws at most {MAX_CHARTS} charts", sql=sql)
    try:
        result = dataset.query(sql)
        spec = chart_spec(arguments, result)
    except (QueryError, ChartError) as error:
        return _error(MAKE_CHART, str(error), sql=sql)
    rows = len(spec["data"]["values"])
    # The model gets no rows back, so it cannot cite numbers that run_sql did not return.
    payload = {"status": "drawn", "mark": spec["mark"]["type"], "rows": rows}
    return json.dumps(payload), ToolStep(
        tool=MAKE_CHART, ok=True, sql=sql, row_count=rows, chart=spec
    )
