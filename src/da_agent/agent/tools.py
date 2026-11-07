"""Tools the minimal agent can call. Failures return as data the model can act on."""

import json
from typing import Any, Protocol

from da_agent.agent.answer import ANSWER_KINDS, ToolStep
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


def tool_specs(rules: RuleIndex | None, dialect: str = "SQLite") -> list[ToolSpec]:
    """search_rules is offered only when there are sections to search. run_sql names the SQL
    dialect of the dataset, so the model writes functions that exist."""
    hint = DIALECT_HINTS.get(dialect, "")
    specs = [
        spec.model_copy(update={"description": spec.description.replace("SQLite", dialect) + hint})
        if spec.name == "run_sql"
        else spec
        for spec in TOOL_SPECS
    ]
    if rules is not None and rules.sections:
        return [*specs, SEARCH_RULES_SPEC]
    return specs


class Dataset(Protocol):
    dialect: str

    def describe(self) -> list[TableInfo]: ...

    def query(self, sql: str) -> QueryResult: ...


def _describe_column(column: Column) -> str:
    text = f"{column.name}:{column.type}"
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


def _error(tool: str, message: str, sql: str | None = None) -> tuple[str, ToolStep]:
    return json.dumps({"error": message}, ensure_ascii=False), ToolStep(
        tool=tool, ok=False, sql=sql, error=message
    )


def execute_tool(
    call: ToolCall, dataset: Dataset, rules: RuleIndex | None = None
) -> tuple[str, ToolStep]:
    """Run a read-only tool. Returns the text for the model and a record of the step."""
    if call.arguments_error:
        return _error(call.name, f"could not read the tool arguments: {call.arguments_error}")
    if call.name == "describe_dataset":
        tables = [_describe_table(table) for table in dataset.describe()]
        return json.dumps({"tables": tables}, ensure_ascii=False), ToolStep(tool=call.name, ok=True)
    if call.name == "run_sql":
        return _run_sql_on(dataset, call.arguments)
    if call.name == SEARCH_RULES and rules is not None and rules.sections:
        return _search_rules(rules, call.arguments)
    known = [spec.name for spec in tool_specs(rules)]
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
