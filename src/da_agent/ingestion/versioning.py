"""Match a new dataset version to the previous one, merge new tables, and limit repairs.

Pure code over reports and specs: no I/O, no model calls. The worker job for adding files uses
these functions (docs/design/2026-11-21-add-files.md). The model proposes; code decides what may
change: existing tables stay as they are, and a repair may touch only what failed on new rows.
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.pipeline import (
    MAX_EXCLUDED_SHARE,
    MIN_PARSE_RATE,
    _record_problems,
)
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import META_COLUMNS, DatasetSpec, RowRule, TableSpec

# A new table sharing at least this share of its columns with an old one is a near match.
NEAR_MATCH_SHARE = 0.5
REPAIRABLE_COLUMN_FIELDS = ("type", "date_formats", "true_values")


class TableHeader(BaseModel):
    raw_name: str
    header: tuple[str, ...]


class TableChange(BaseModel):
    raw_name: str
    table: str | None = None
    kind: Literal["unchanged", "grown", "new", "removed"]
    previous_raw_name: str | None = None
    near_match: str | None = None
    missing_columns: list[str] = []
    extra_columns: list[str] = []


def headers(report: IngestReport) -> list[TableHeader]:
    return [
        TableHeader(
            raw_name=t.name,
            header=tuple(c.name for c in t.columns if c.name not in META_COLUMNS),
        )
        for t in report.tables
    ]


def file_of(source: str) -> str:
    """The stored file name of a source label such as `book.xlsx#Sheet1`, as build.py's
    `_file_of` takes it from `_source`."""
    return source.split("#", 1)[0]


def _near_match(header: tuple[str, ...], previous: list[TableHeader]) -> TableHeader | None:
    best, best_shared = None, 0
    for old in previous:
        shared = len(set(header) & set(old.header))
        if shared > best_shared and shared >= len(header) * NEAR_MATCH_SHARE:
            best, best_shared = old, shared
    return best


def match_tables(
    previous: list[TableHeader],
    current: IngestReport,
    new_sources: frozenset[str],
    spec: DatasetSpec,
) -> list[TableChange]:
    """Pair each raw table with the previous one that has the same header, in order. A raw table
    is `grown` when some of its sources are new, `unchanged` otherwise, and `new` when no old
    header equals it. An old header that nothing equals is `removed`."""
    table_of = {t.source: t.name for t in spec.tables}
    free = list(previous)
    changes: list[TableChange] = []
    for raw, header in zip(current.tables, headers(current), strict=True):
        match = next((old for old in free if old.header == header.header), None)
        if match is not None:
            free.remove(match)
            has_new = any(file_of(s.source) in new_sources for s in raw.sources)
            changes.append(
                TableChange(
                    raw_name=raw.name,
                    table=table_of.get(match.raw_name),
                    kind="grown" if has_new else "unchanged",
                    previous_raw_name=match.raw_name,
                )
            )
            continue
        near = _near_match(header.header, previous)
        change = TableChange(raw_name=raw.name, kind="new")
        if near is not None:
            change.near_match = near.raw_name
            change.missing_columns = [c for c in near.header if c not in header.header]
            change.extra_columns = [c for c in header.header if c not in near.header]
        changes.append(change)
    for old in free:
        changes.append(
            TableChange(
                raw_name=old.raw_name,
                table=table_of.get(old.raw_name),
                kind="removed",
                previous_raw_name=old.raw_name,
            )
        )
    return changes


def rename_sources(spec: DatasetSpec, changes: list[TableChange]) -> DatasetSpec:
    """Point each kept table at its new raw name, and drop removed tables and their links."""
    renames = {
        c.previous_raw_name: c.raw_name
        for c in changes
        if c.kind in ("unchanged", "grown") and c.previous_raw_name
    }
    removed = {c.previous_raw_name for c in changes if c.kind == "removed"}
    dropped = {t.name for t in spec.tables if t.source in removed}
    tables = [
        t.model_copy(update={"source": renames.get(t.source, t.source)})
        for t in spec.tables
        if t.name not in dropped
    ]
    relationships = [
        r for r in spec.relationships if r.from_table not in dropped and r.to_table not in dropped
    ]
    return spec.model_copy(update={"tables": tables, "relationships": relationships})


@dataclass(frozen=True)
class QualityProblem:
    """`key` is `columns.<source>` or `exclude_rows.<position>`, the keys `repair_diff` takes."""

    key: str
    message: str


def quality_problems(report: BuildReport, grown: set[str]) -> dict[str, list[QualityProblem]]:
    """Problems of the rows from new files in each grown table, judged like `parse_problems`.

    The share of excluded rows uses the new files' raw rows, which the report does not count
    directly: the clean rows from new files plus the rows their exclude rules removed.
    """
    found: dict[str, list[QualityProblem]] = {}
    for table in report.tables:
        if table.name not in grown:
            continue
        problems: list[QualityProblem] = []
        raw_new = table.new_rows + sum(r.rows for r in table.new_excluded)
        for rule in table.new_excluded:
            messages = _record_problems(table.name, raw_new, rule, MAX_EXCLUDED_SHARE)
            if messages:
                problems.append(
                    QualityProblem(f"exclude_rows.{rule.position}", "; ".join(messages))
                )
        for column in table.new_columns:
            if column.non_blank and column.parse_rate < MIN_PARSE_RATE:
                message = (
                    f"{table.name}.{column.name} ({column.type}): parsed "
                    f"{column.parse_rate:.0%} of {column.non_blank} values in the new files; "
                    f"failing values: {column.failed_samples}"
                )
                problems.append(QualityProblem(f"columns.{column.source}", message))
        if problems:
            found[table.name] = problems
    return found


def merge_new_tables(current: DatasetSpec, proposed: DatasetSpec, new_raw: set[str]) -> DatasetSpec:
    """Keep every current table as it is. Add only proposed tables over new raw tables, without
    `drop_overlap` (code writes that), and only relationships that touch a new table."""
    taken = {t.name for t in current.tables}
    added: list[TableSpec] = []
    for table in proposed.tables:
        if table.source in new_raw and table.name not in taken:
            taken.add(table.name)
            added.append(table.model_copy(update={"drop_overlap": []}))
    new_names = {t.name for t in added}
    relationships = [
        r
        for r in proposed.relationships
        if (r.from_table in new_names or r.to_table in new_names)
        and r.from_table in taken
        and r.to_table in taken
    ]
    return current.model_copy(
        update={
            "tables": [*current.tables, *added],
            "relationships": [*current.relationships, *relationships],
        }
    )


class RepairChange(BaseModel):
    table: str
    path: str
    before: Any
    after: Any


def repair_diff(
    current: DatasetSpec, proposed: DatasetSpec, failing: dict[str, set[str]]
) -> list[RepairChange]:
    """The changes a repair proposal may make: type, date formats, and true values of failing
    columns, and failing exclude rules. Everything else is left out."""
    changes: list[RepairChange] = []
    proposed_by_source = {t.source: t for t in proposed.tables}
    for table in current.tables:
        keys = failing.get(table.name, set())
        other = proposed_by_source.get(table.source)
        if not keys or other is None:
            continue
        other_columns = {c.source: c for c in other.columns}
        for column in table.columns:
            twin = other_columns.get(column.source)
            if twin is None or f"columns.{column.source}" not in keys:
                continue
            for field in REPAIRABLE_COLUMN_FIELDS:
                before, after = getattr(column, field), getattr(twin, field)
                if before != after:
                    path = f"columns.{column.source}.{field}"
                    changes.append(
                        RepairChange(table=table.name, path=path, before=before, after=after)
                    )
        for position, rule in enumerate(table.exclude_rows, start=1):
            if f"exclude_rows.{position}" not in keys:
                continue
            after_rule = (
                other.exclude_rows[position - 1] if position <= len(other.exclude_rows) else None
            )
            if after_rule != rule:
                path = f"exclude_rows.{position}"
                changes.append(
                    RepairChange(table=table.name, path=path, before=rule, after=after_rule)
                )
    return changes


def apply_repairs(spec: DatasetSpec, accepted: list[RepairChange]) -> DatasetSpec:
    """Apply accepted changes to a copy of the spec. An exclude rule changed to None is removed."""
    tables = []
    for table in spec.tables:
        mine = [c for c in accepted if c.table == table.name]
        columns = list(table.columns)
        rules: list[RowRule | None] = list(table.exclude_rows)
        for change in mine:
            if change.path.startswith("exclude_rows."):
                position = int(change.path.split(".", 1)[1])
                if 1 <= position <= len(rules):
                    after = change.after
                    rules[position - 1] = None if after is None else RowRule.model_validate(after)
                continue
            source_and_field = change.path.removeprefix("columns.")
            source, field = source_and_field.rsplit(".", 1)
            if field not in REPAIRABLE_COLUMN_FIELDS:
                continue
            columns = [
                c.model_copy(update={field: change.after}) if c.source == source else c
                for c in columns
            ]
        tables.append(
            table.model_copy(
                update={"columns": columns, "exclude_rows": [r for r in rules if r is not None]}
            )
        )
    return spec.model_copy(update={"tables": tables})
