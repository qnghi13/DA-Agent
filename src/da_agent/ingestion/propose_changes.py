"""Ask a model for the tables a new dataset version brings, and for narrow repairs of new rows.

Runs in the Worker, never in the sandbox. The model proposes and code limits it
(docs/design/2026-11-21-add-files.md): existing tables stay as they are, `drop_overlap` is written
by code only, and a repair may change only the type, date formats, and true values of failing
columns and the failing exclude rules. Profiles, cell values, and documents are data; each sits in
its own delimited block, and sample values of sensitive columns never reach the prompt.
"""

import json
from collections.abc import Callable

from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
from da_agent.ingestion.propose import (
    MAX_OUTPUT_TOKENS,
    SYSTEM_PROMPT,
    ProposalResult,
    profile_for_prompt,
)
from da_agent.ingestion.propose import (
    _parse as _parse_spec,
)
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import DatasetSpec, TableSpec, sanitize_spec
from da_agent.ingestion.versioning import (
    QualityProblem,
    RepairChange,
    apply_repairs,
    merge_new_tables,
    repair_diff,
)
from da_agent.llm.client import LLMClient, RunBudget
from da_agent.llm.types import AllRoutesFailedError, LLMError, LLMRequest, Message

NEW_TABLES_PROMPT = """\

This is a new version of an existing dataset: the user added files. The tables already in \
<existing_spec> are final and read-only. Propose specs only for the raw tables in <raw_profile>, \
which are new. You may link a new table to an existing one in "relationships", using the clean \
names in <existing_spec>. Never write "drop_overlap". Reply with a JSON object holding the new \
tables and their relationships.
"""

REPAIR_PROMPT = """\

You repair a table spec. Rows from newly added files failed quality checks. The spec in \
<existing_spec> is read-only context. For each failing table in <failures>, reply with a JSON \
object in the same format, holding that table with its "source" and "name" unchanged, with the \
same columns and rules in the same order. Change only what failed: the "type", "date_formats", or \
"true_values" of a failing column, or a failing exclude rule. Every other change is discarded. \
A failing column's profile shows how its values on the new rows parsed; a rule's profile shows \
how many rows it removed. Never write "drop_overlap".
"""


def _parse(text: str | None) -> tuple[DatasetSpec, list[str]]:
    """Parse a reply like `propose_spec`, after removing any `drop_overlap` the model wrote: code
    owns it, and a malformed one would otherwise drop its whole table."""
    content = text or ""
    start = content.find("{")
    if start < 0:
        return _parse_spec(text)
    try:
        data, _ = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError:
        return _parse_spec(text)
    if isinstance(data, dict) and isinstance(data.get("tables"), list):
        for table in data["tables"]:
            if isinstance(table, dict):
                table.pop("drop_overlap", None)
    return _parse_spec(json.dumps(data))


def _existing_spec_for_prompt(current: DatasetSpec) -> str:
    tables = [
        {
            "name": t.name,
            "source": t.source,
            "columns": [{"name": c.name, "type": c.type} for c in t.columns],
        }
        for t in current.tables
    ]
    relationships = [r.model_dump() for r in current.relationships]
    return json.dumps({"tables": tables, "relationships": relationships}, ensure_ascii=False)


def propose_new_tables(
    report: IngestReport,
    new_raw: set[str],
    current: DatasetSpec,
    documents: list[dict[str, str]],
    llm: LLMClient,
    *,
    run_id: str,
    validate: Callable[[DatasetSpec], list[str]],
    max_attempts: int = 3,
    budget: RunBudget | None = None,
) -> ProposalResult:
    """Return the current spec plus the first proposal for the new tables without problems, or
    the last one with its problems. The result's spec is already merged."""
    only_new = report.model_copy(update={"tables": [t for t in report.tables if t.name in new_raw]})
    parts = [
        "Raw tables new in this version, one JSON object per line:",
        f"<raw_profile>\n{profile_for_prompt(only_new)}\n</raw_profile>",
        "Existing spec (read-only):",
        f"<existing_spec>\n{_existing_spec_for_prompt(current)}\n</existing_spec>",
    ]
    for document in documents:
        parts.append(f'<document name="{document["path"]}">\n{document["text"]}\n</document>')
    parts.append("Reply with the JSON object for the new tables.")
    messages = [
        Message(role="system", content=SYSTEM_PROMPT + NEW_TABLES_PROMPT),
        Message(role="user", content="\n\n".join(parts)),
    ]
    best = ProposalResult(spec=current, attempts=0)
    for attempt in range(1, max_attempts + 1):
        best.attempts = attempt
        request = LLMRequest(messages=messages, max_output_tokens=MAX_OUTPUT_TOKENS)
        try:
            response = llm.complete(request, run_id=run_id, budget=budget)
        except AllRoutesFailedError:
            raise  # an outage, for the caller to retry; never a proposal problem
        except LLMError as error:
            best.error = str(error)[:500]
            return best
        proposed, problems = _parse(response.content)
        proposed, dropped = sanitize_spec(proposed, report)
        problems += dropped
        merged = merge_new_tables(current, proposed, new_raw)
        if len(merged.tables) > len(current.tables):
            problems += validate(merged)
            best.spec, best.problems = merged, problems
            if not problems:
                return best
        else:
            problems.append(f"the reply has no table for the new raw tables {sorted(new_raw)}")
            best.problems = problems
        messages.append(Message(role="assistant", content=response.content))
        feedback = "\n".join(f"- {problem}" for problem in problems)
        messages.append(
            Message(
                role="user",
                content=f"Fix these problems and reply with the new tables:\n{feedback}",
            )
        )
    return best


def _column_for_prompt(column: ColumnBuild, sensitive: bool) -> dict[str, object]:
    entry: dict[str, object] = {
        "source": column.source,
        "type": column.type,
        "non_blank": column.non_blank,
        "parse_rate": round(column.parse_rate, 3),
    }
    if sensitive:
        entry["sensitive"] = True
    else:
        entry["failed_samples"] = column.failed_samples
    return entry


def _failure_for_prompt(
    table: TableBuild, spec: TableSpec | None, problems: list[QualityProblem]
) -> dict[str, object]:
    keys = {p.key for p in problems}
    sensitive = {c.source for c in spec.columns if c.sensitive} if spec else set()
    columns = [
        _column_for_prompt(c, c.source in sensitive)
        for c in table.new_columns
        if f"columns.{c.source}" in keys
    ]
    rules = [
        {"position": r.position, "reason": r.reason, "rows_removed": r.rows}
        for r in table.new_excluded
        if f"exclude_rows.{r.position}" in keys
    ]
    return {
        "table": table.name,
        "new_rows": table.new_rows,
        "problems": [p.message for p in problems],
        "failing_columns": columns,
        "failing_rules": rules,
    }


def _table_spec_for_prompt(spec: TableSpec) -> dict[str, object]:
    return {
        "source": spec.source,
        "name": spec.name,
        "columns": [
            c.model_dump(exclude_defaults=True) | {"source": c.source, "name": c.name}
            for c in spec.columns
        ],
        "exclude_rows": [r.model_dump() for r in spec.exclude_rows],
    }


def propose_repairs(
    report: BuildReport,
    failing: dict[str, list[QualityProblem]],
    current: DatasetSpec,
    llm: LLMClient,
    *,
    run_id: str,
    validate: Callable[[DatasetSpec], list[str]],
    max_attempts: int = 3,
    budget: RunBudget | None = None,
) -> tuple[list[RepairChange], str | None]:
    """Return the changes of the first proposal that passes `validate`, or `([], reason)`."""
    by_name = {t.name: t for t in current.tables}
    builds = {t.name: t for t in report.tables}
    failures = [
        _failure_for_prompt(builds[name], by_name.get(name), problems)
        for name, problems in failing.items()
        if name in builds
    ]
    context = [
        json.dumps(_table_spec_for_prompt(by_name[name]), ensure_ascii=False)
        for name in failing
        if name in by_name
    ]
    prompt = "\n\n".join(
        [
            "Failures on new rows, one JSON object per line:",
            "<failures>\n"
            + "\n".join(json.dumps(f, ensure_ascii=False) for f in failures)
            + "\n</failures>",
            "Table specs (read-only), one JSON object per line:",
            "<existing_spec>\n" + "\n".join(context) + "\n</existing_spec>",
            "Reply with the JSON object for the failing tables.",
        ]
    )
    messages = [
        Message(role="system", content=SYSTEM_PROMPT + REPAIR_PROMPT),
        Message(role="user", content=prompt),
    ]
    keys = {name: {p.key for p in problems} for name, problems in failing.items()}
    last = "no proposal"
    for _ in range(max_attempts):
        request = LLMRequest(messages=messages, max_output_tokens=MAX_OUTPUT_TOKENS)
        try:
            response = llm.complete(request, run_id=run_id, budget=budget)
        except AllRoutesFailedError:
            raise  # an outage, for the caller to retry; never a repair problem
        except LLMError as error:
            return [], str(error)[:500]
        proposed, problems = _parse(response.content)
        changes = repair_diff(current, proposed, keys)
        if changes:
            problems += validate(apply_repairs(current, changes))
            if not problems:
                return changes, None
        else:
            problems.append("no change to the failing columns or rules was found in the reply")
        last = "; ".join(problems)[:500]
        messages.append(Message(role="assistant", content=response.content))
        feedback = "\n".join(f"- {problem}" for problem in problems)
        messages.append(
            Message(
                role="user",
                content=f"Fix these problems and reply with the failing tables:\n{feedback}",
            )
        )
    return [], last
