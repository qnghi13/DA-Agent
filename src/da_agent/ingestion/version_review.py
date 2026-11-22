"""What a version check found, and how the user's choices change the spec.

Pure code over reports and specs, used by the version job (docs/design/2026-11-21-add-files.md).
Code, never the model, writes `drop_overlap` and names tables that have no spec entry.
"""

from typing import Any

from da_agent.ingestion.build import BuildReport, TableBuild, _with_missing_tables
from da_agent.ingestion.report import IngestReport, TableReport
from da_agent.ingestion.spec import DatasetSpec, OverlapDrop, TableSpec
from da_agent.ingestion.versioning import QualityProblem, TableChange, TableHeader, file_of
from da_agent.reports.metric import Definition, is_stale

ALL_CHOICES = ["replace", "drop_overlap", "keep_both"]
# Without a key or exact repeat there is no row to drop: a date-range overlap names no row pair.
NO_DROP_CHOICES = ["replace", "keep_both"]


def _names(spec: DatasetSpec, raw_names: list[str]) -> dict[str, str]:
    """Each raw table's clean name as the build gives it: the spec's, else one made from the raw
    name, by the build's own rule."""
    report = IngestReport(
        tables=[TableReport(name=n, sources=[], row_count=0, columns=[]) for n in raw_names]
    )
    return {t.source: t.name for t in _with_missing_tables(spec, report) if t.source in raw_names}


def table_names(spec: DatasetSpec, previous: list[TableHeader]) -> dict[str, str]:
    """The clean name of each raw table of a version, given its spec and headers."""
    return _names(spec, [h.raw_name for h in previous])


def pin_kept_tables(
    renamed: DatasetSpec,
    current: DatasetSpec,
    previous: list[TableHeader],
    changes: list[TableChange],
) -> DatasetSpec:
    """Give each kept table that has no spec entry one with the name it had. The build names such
    a table after its raw group, and a new file that sorts first renames the group."""
    before = table_names(current, previous)
    listed = {t.source for t in renamed.tables}
    added = [
        TableSpec(source=c.raw_name, name=before[c.previous_raw_name])
        for c in changes
        if c.kind in ("unchanged", "grown")
        and c.raw_name not in listed
        and c.previous_raw_name in before
    ]
    if not added:
        return renamed
    return renamed.model_copy(update={"tables": [*renamed.tables, *added]})


def cover_raw_tables(spec: DatasetSpec, report: IngestReport) -> tuple[DatasetSpec, list[str]]:
    """Give every raw table a spec entry, named as the build would name it. Returns the names
    added, so nothing is left without a spec when a proposal missed a table."""
    tables = _with_missing_tables(spec, report)
    added = [t.name for t in tables[len(spec.tables) :]]
    if not added:
        return spec, []
    return spec.model_copy(update={"tables": tables}), added


def _overlaps(build: TableBuild | None, spec: TableSpec | None) -> list[dict[str, Any]]:
    """Overlap findings as stored. An exact repeat on a table whose approved spec already removes
    exact duplicates is reported as removed by that rule, not asked about."""
    dedupe = spec is not None and spec.dedupe == "exact"
    entries = []
    for overlap in build.overlaps if build else []:
        entry = overlap.model_dump(mode="json")
        if dedupe and overlap.kind == "exact":
            entry["removed_by_dedupe"] = True
        entries.append(entry)
    return entries


def source_files(report: IngestReport) -> dict[str, list[str]]:
    """The stored files each raw table of a version is read from, in report order."""
    return {
        t.name: list(dict.fromkeys(file_of(s.source) for s in t.sources)) for t in report.tables
    }


def version_changes(
    changes: list[TableChange],
    spec: DatasetSpec,
    names_before: dict[str, str],
    report: BuildReport,
    quality: dict[str, list[QualityProblem]],
    stale: dict[str, list[str]],
    *,
    sources: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """One entry per table: what the check found, for the review and the build step. `files`
    lists the table's own stored files (`sources`, per raw table): the only ones a `replace`
    choice on it may name. A removed table has none in this version."""
    by_source = {t.source: t for t in spec.tables}
    builds = {t.source: t for t in report.tables}
    entries = []
    for change in changes:
        removed = change.kind == "removed"
        raw = change.previous_raw_name or change.raw_name
        table_spec = None if removed else by_source.get(change.raw_name)
        if removed:
            name = names_before.get(raw, raw)
        else:
            name = table_spec.name if table_spec else change.raw_name
        build = None if removed else builds.get(change.raw_name)
        near = change.near_match
        overlaps = _overlaps(build, table_spec)
        entry: dict[str, Any] = {
            "table": name,
            "kind": change.kind,
            "new_rows": build.new_rows if build else 0,
            "overlaps": overlaps,
            "near_match": names_before.get(near, near) if near else None,
            "missing_columns": list(change.missing_columns),
            "extra_columns": list(change.extra_columns),
            "quality": [p.message for p in quality.get(name, [])],
            "stale_metrics": stale.get(name, []),
            "files": [] if removed else list((sources or {}).get(change.raw_name, [])),
        }
        if overlaps:
            entry["allowed_choices"] = _allowed_choices(overlaps)
        entries.append(entry)
    return entries


def _allowed_choices(overlaps: list[dict[str, Any]]) -> list[str]:
    """`drop_overlap` only when a key or exact repeat names the rows to drop."""
    droppable = any(
        o.get("kind") in ("exact", "key") and not o.get("removed_by_dedupe") for o in overlaps
    )
    return list(ALL_CHOICES if droppable else NO_DROP_CHOICES)


def invalid_choices(entries: list[dict[str, Any]], chosen: dict[str, Any]) -> list[str]:
    """Tables whose choice the build step cannot carry out: a blocking table without a choice, a
    choice its overlaps do not allow, or a table the check did not report."""
    allowed = {e["table"]: e.get("allowed_choices", NO_DROP_CHOICES) for e in entries}
    missing = blocking_tables(entries) - set(chosen)
    wrong = {t for t, c in chosen.items() if t not in allowed or c not in allowed[t]}
    return sorted(missing | wrong)


def blocking_tables(entries: list[dict[str, Any]]) -> set[str]:
    """Tables whose new rows overlap older rows in a way the user must decide on."""
    return {
        e["table"]
        for e in entries
        if any(not o.get("removed_by_dedupe") for o in e.get("overlaps", []))
    }


def overlap_keys(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The key each table's key overlap was found on, for `drop_overlap`."""
    keys = {}
    for entry in entries:
        for overlap in entry.get("overlaps", []):
            if overlap.get("kind") == "key" and overlap.get("key"):
                keys[entry["table"]] = [str(k) for k in overlap["key"]]
    return keys


def stale_metrics(
    metrics: list[tuple[str, dict[str, Any]]], removed: set[str], built: BuildReport
) -> dict[str, list[str]]:
    """Saved metrics on removed tables that the new build can no longer compute, per table."""
    visible = {t.name: {c.name for c in t.columns} for t in built.tables if t.error is None}
    found: dict[str, list[str]] = {}
    for name, data in metrics:
        definition = Definition.from_json(data)
        if definition.table in removed and is_stale(definition, visible):
            found.setdefault(definition.table, []).append(name)
    return found


def replaced_files(
    chosen: dict[str, str],
    names: dict[str, Any],
    current_files: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> set[str]:
    """The stored names of the files the user chose to replace, per table with a `replace`
    choice. Only a file of the current version that is one of that table's own files (its
    entry's `files`) can be replaced: another table's file would remove that table unasked.
    Anything else is a ValueError. The API and the build step share this rule."""
    older = {f.get("stored_as") for f in current_files}
    own = {e["table"]: set(e.get("files") or []) for e in entries}
    replaced = set()
    for table, choice in chosen.items():
        if choice != "replace":
            continue
        name = names.get(table)
        if not isinstance(name, str) or name not in older:
            raise ValueError(f"no file of the current version to replace for {table!r}")
        if name not in own.get(table, set()):
            raise ValueError(f"{name} is not a file of {table!r}")
        replaced.add(name)
    return replaced


def with_overlap_drops(
    spec: DatasetSpec,
    tables: set[str],
    report: IngestReport,
    new_files: list[str],
    keys: dict[str, list[str]],
) -> DatasetSpec:
    """For each table the user chose `drop_overlap` on, drop the rows of each new file that repeat
    a row of the table's older files or of a new file before it in upload order (`new_files`):
    by the key the check found, else by whole row."""
    raw_tables = {t.name: t for t in report.tables}
    updated = []
    for table in spec.tables:
        raw = raw_tables.get(table.source)
        if table.name in tables and raw is not None:
            files = set(file_of(s.source) for s in raw.sources)
            older = sorted(files - set(new_files))
            newer = [f for f in new_files if f in files]
            key = keys.get(table.name, [])
            drops = [
                OverlapDrop(file=f, against=[*older, *newer[:i]], key=key)
                for i, f in enumerate(newer)
                if older or i
            ]
            if drops:
                table = table.model_copy(update={"drop_overlap": [*table.drop_overlap, *drops]})
        updated.append(table)
    return spec.model_copy(update={"tables": updated})
