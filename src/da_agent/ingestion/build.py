# ruff: noqa: S608 - this module compiles SQL on purpose. Identifiers go through q() and literals
# through lit(), and tests/unit/test_dataset_build.py covers hostile names and values.
"""Build clean tables from raw tables and a DatasetSpec. Runs inside the sandbox.

Every identifier is quoted and every literal escaped, and only values from the spec's fixed choices
reach the SQL, so a spec cannot inject SQL. Values that fail to parse become NULL and are copied to
`_quarantine` with their source and row, and rows that exclude rules remove, such as subtotals, are
copied to `_excluded`, so nothing is dropped silently.
"""

import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field

from da_agent.ingestion.raw import slug
from da_agent.ingestion.report import IngestReport, RowShape, TableReport
from da_agent.ingestion.spec import (
    META_COLUMNS,
    SOURCE_NAME,
    ColumnSpec,
    DatasetSpec,
    OverlapDrop,
    RowCondition,
    RowRule,
    TableSpec,
)
from da_agent.ingestion.sqltext import (
    filled_count_sql,
    filled_sql,
    lit,
    number_sql,
    q,
    shape_sql,
    signature_sql,
    subtotal_rows_sql,
)

CLEAN_DATABASE = "dataset.duckdb"
QUARANTINE = "_quarantine"
EXCLUDED = "_excluded"
FAILED_SAMPLES = 5
# Text columns with this few distinct values list them, so the agent filters on real values
# instead of guessing them from the question's language. Longer values look like free text.
MAX_KNOWN_VALUES, MAX_KNOWN_VALUE_CHARS = 12, 60
# A key of at most this many columns that identifies every distinct row proves that exact repeats
# are double entries. Tables with more columns than KEY_SEARCH_COLUMNS search only the first ones.
MAX_KEY_SIZE, KEY_SEARCH_COLUMNS = 2, 40
DEFAULT_TRUE = ("true", "1", "yes", "y", "x", "có", "co", "đúng", "dung", "rồi")
DEFAULT_FALSE = ("false", "0", "no", "n", "không", "khong", "sai", "chưa")
ISO_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
DATE_FORMATS = {
    "dmy": ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"),
    "mdy": ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m-%d-%Y"),
    "ymd": ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d"),
}
VI_TEXT_DATE = r"^(\d{1,2})\s+(thg|tháng)\s*(\d{1,2}),?\s+(\d{4})$"
# A rare shape counts as subtotals when at least this share of its rows, and at least two, equal
# the sum of the rows above them.
SUBTOTAL_SHARE = 0.5
# Rows of such a shape that equal the sum of at least this many record values above them are
# proven subtotals, and code excludes them whatever the spec's rules say (ADR 0029). One value is
# not enough: a sale may repeat the amount of the sale before it.
PROVEN_SUBTOTAL_MIN_ROWS, PROVEN_SUBTOTAL_ROUNDS = 2, 3
PROVEN_SUBTOTAL = "Proven subtotal: equals the sum of the rows above (code)"
# An identifier column, such as an order or slip id, is filled in at least half of the kept rows,
# nearly unique there, and mostly not a number. Rows that carry one are records.
ID_FILLED, ID_DISTINCT, ID_NUMERIC, ID_MIN_ROWS = 0.5, 0.9, 0.5, 20
# The column profile lists this many most frequent values, each cut to this length.
TOP_VALUES, TOP_VALUE_CHARS = 5, 60
# A key identifies the older rows when its distinct values reach this share of them. The overlap
# report lists this many example rows, each with at most this many columns.
OVERLAP_KEY_SHARE, OVERLAP_EXAMPLES, OVERLAP_EXAMPLE_COLUMNS = 0.99, 5, 8
# At most this many earlier new files are named on one overlap.
MAX_NAMED_FILES = 20
NUMBER_TYPES = ("integer", "number", "money", "quantity")
# Excel serial numbers from 1954 to 2119. Five-digit numbers outside this range stay unparsed.
EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX = 20000, 80000


class ValueCount(BaseModel):
    value: str
    rows: int


class ColumnBuild(BaseModel):
    name: str
    source: str
    type: str
    non_blank: int = 0
    parsed: int = 0
    failed_samples: list[str] = Field(default_factory=list)
    values: list[str] = Field(
        default_factory=list, description="Every distinct value of a text column with few of them."
    )
    min_value: str | None = Field(default=None, description="Earliest value of a date column.")
    max_value: str | None = Field(default=None, description="Latest value of a date column.")
    # The column profile people see (docs/design/2026-11-14-data-overview.md). The agent's prompt
    # does not read these fields.
    distinct: int | None = Field(default=None, description="Distinct values in the clean table.")
    number_min: float | None = None
    number_max: float | None = None
    number_mean: float | None = None
    top_values: list[ValueCount] = Field(
        default_factory=list, description="The most frequent values of a text or boolean column."
    )

    @property
    def parse_rate(self) -> float:
        return self.parsed / self.non_blank if self.non_blank else 1.0


class ExcludedRule(BaseModel):
    reason: str
    position: int = Field(default=0, description="1-based index in the table's exclude_rows.")
    rows: int = 0
    # Excluded rows with as many filled cells as the table's common row shape: likely records.
    full_rows: int = 0
    # Excluded rows with a value in an identifier column, such as an order id: likely records.
    with_identifier: int = 0
    identifier: str | None = None


class Overlap(BaseModel):
    """New rows that repeat older ones: whole rows, a key, or a date inside the old date range."""

    kind: Literal["exact", "key", "date"]
    rows: int
    key: list[str] = Field(default_factory=list, description="Raw key columns, for kind key.")
    column: str | None = Field(default=None, description="Clean date column, for kind date.")
    examples: list[dict[str, str | None]] = Field(default_factory=list)
    earlier_new_files: list[str] = Field(
        default_factory=list,
        description="Files new in the same upload, earlier in upload order, that already hold "
        "some of these rows. Empty when the rows repeat only older files.",
    )


class SubtotalCheck(BaseModel):
    """A rare row shape whose rows mostly equal the sum of the rows above them."""

    columns: list[str]
    rows: int
    sums_above: int
    kept: int = Field(description="Rows of this shape still in the clean table.")


class TableBuild(BaseModel):
    name: str
    source: str
    description: str = Field(default="", description="What one row is, from the spec.")
    raw_rows: int = 0
    rows: int = 0
    columns: list[ColumnBuild] = Field(default_factory=list)
    hidden: list[str] = Field(default_factory=list)
    excluded: list[ExcludedRule] = Field(default_factory=list)
    subtotal_checks: list[SubtotalCheck] = Field(default_factory=list)
    proven_subtotals: int = Field(
        default=0, description="Rows code excluded as proven subtotals, beyond the spec's rules."
    )
    duplicate_rows: int = Field(default=0, description="Rows that repeat an earlier row exactly.")
    duplicate_key: list[str] = Field(
        default_factory=list,
        description="Fewer columns than the table that identify every distinct row, if any.",
    )
    # Set only when the build knows which files are new (docs/design/2026-11-21-add-files.md).
    overlaps: list[Overlap] = Field(default_factory=list)
    new_rows: int = Field(default=0, description="Rows in the clean table from new files.")
    new_columns: list[ColumnBuild] = Field(
        default_factory=list, description="Parse stats over the rows from new files only."
    )
    new_excluded: list[ExcludedRule] = Field(
        default_factory=list, description="The excluded rows of new files only."
    )
    new_overlap_dropped: int = Field(
        default=0,
        description="Rows of new files the user chose to drop as overlaps: known duplicates, "
        "counted in the new files' raw rows but never judged as an exclude rule.",
    )
    error: str | None = None


class BuildReport(BaseModel):
    tables: list[TableBuild] = Field(default_factory=list)


def _timestamp(text: str, formats: list[str]) -> str:
    """ISO, Excel serial numbers, and "1 thg 1 2025" are unambiguous, so they are always accepted.
    The spec chooses between day-first and month-first, and allows day/month without a year,
    which takes its year from the sheet or file name, as in "T04_2025"."""
    value = f"trim({text})"
    patterns = list(ISO_FORMATS)
    for name in formats:
        patterns.extend(DATE_FORMATS.get(name, ()))
    parts = [f"try_strptime({value}, [{', '.join(lit(p) for p in patterns)}])"]
    parts.append(
        f"CASE WHEN regexp_matches({value}, '^\\d{{5}}(\\.\\d+)?$') "
        f"AND TRY_CAST({value} AS DOUBLE) BETWEEN {EXCEL_SERIAL_MIN} AND {EXCEL_SERIAL_MAX} "
        f"THEN TIMESTAMP '1899-12-30 00:00:00' + "
        f"to_microseconds(CAST(round(TRY_CAST({value} AS DOUBLE) * 86400000000) AS BIGINT)) END"
    )
    year, month, day = (_vi_date_part(value, n) for n in (4, 3, 1))
    parts.append(f"CAST(try(make_date({year}, {month}, {day})) AS TIMESTAMP)")
    if "dm" in formats:
        day_month = r"^(\d{1,2})[/.-](\d{1,2})$"
        day = f"TRY_CAST(regexp_extract({value}, {lit(day_month)}, 1) AS INTEGER)"
        month = f"TRY_CAST(regexp_extract({value}, {lit(day_month)}, 2) AS INTEGER)"
        year = "TRY_CAST(regexp_extract(_source, '((19|20)\\d{2})', 1) AS INTEGER)"
        parts.append(f"CAST(try(make_date({year}, {month}, {day})) AS TIMESTAMP)")
    return f"COALESCE({', '.join(parts)})"


def _vi_date_part(value: str, group: int) -> str:
    return f"TRY_CAST(regexp_extract(lower({value}), {lit(VI_TEXT_DATE)}, {group}) AS INTEGER)"


def parse_expression(column: ColumnSpec, raw: str) -> str:
    """SQL that turns one raw text column into the spec's type, or NULL when it cannot."""
    text = q(raw)
    kind = column.type
    if kind == "text":
        return text
    if kind == "number":
        return number_sql(text)
    if kind in ("integer", "money"):
        number = number_sql(text)
        if kind == "money":
            return f"CAST(round({number}) AS BIGINT)"
        return f"CASE WHEN {number} = floor({number}) THEN CAST({number} AS BIGINT) END"
    if kind == "quantity":
        leading = f"regexp_extract(trim({text}), '^(-?\\d+(?:[.,]\\d+)?)', 1)"
        return number_sql(f"NULLIF({leading}, '')")
    if kind in ("date", "datetime"):
        stamp = _timestamp(text, list(column.date_formats))
        return f"CAST({stamp} AS DATE)" if kind == "date" else stamp
    true_values = [v.lower() for v in column.true_values] or list(DEFAULT_TRUE)
    value = f"lower(trim({text}))"
    return (
        f"CASE WHEN {value} IN ({', '.join(lit(v) for v in true_values)}) THEN true "
        f"WHEN {value} IN ({', '.join(lit(v) for v in DEFAULT_FALSE)}) THEN false END"
    )


def _condition(condition: RowCondition) -> str:
    """SQL for one raw-cell test. Text tests are trimmed and case-insensitive."""
    cell = q(condition.column)
    blank = f"({cell} IS NULL OR trim({cell}) = '')"
    if condition.op == "blank":
        return blank
    if condition.op == "not_blank":
        return f"NOT {blank}"
    text, value = f"lower(trim({cell}))", lit(condition.value.strip().lower())
    if condition.op == "equals":
        return f"coalesce({text} = {value}, false)"
    if condition.op == "starts_with":
        return f"coalesce(starts_with({text}, {value}), false)"
    return f"coalesce(contains({text}, {value}), false)"


def _rule_labels(rules: list[RowRule]) -> list[str]:
    """One distinct label per rule: its reason, numbered when two rules share one."""
    labels: list[str] = []
    for position, rule in enumerate(rules, start=1):
        label = rule.reason or f"rule {position}"
        labels.append(label if label not in labels else f"{label} ({position})")
    return labels


def _exclusion_reason(rules: list[RowRule]) -> str:
    """SQL naming the first rule a row matches, or NULL when it matches none."""
    if not rules:
        return "CAST(NULL AS VARCHAR)"
    branches = " ".join(
        f"WHEN {' AND '.join(_condition(c) for c in rule.when)} THEN {lit(label)}"
        for rule, label in zip(rules, _rule_labels(rules), strict=True)
    )
    return f"CASE {branches} END"


def build(
    raw_database: Path,
    spec: DatasetSpec,
    report: IngestReport,
    out_dir: Path,
    new_sources: Sequence[str] = (),
) -> BuildReport:
    """Write dataset.duckdb to out_dir. One failing table is reported and the rest still build.

    A row is new when the file part of its `_source` (the text before `#`) is in new_sources.
    new_sources is in upload order: each new file is checked against older files and the new
    files before it, so two copies sent in one upload are found too."""
    new_sources = tuple(dict.fromkeys(new_sources))
    result = BuildReport()
    raw_columns = {
        t.name: [c.name for c in t.columns if c.name not in META_COLUMNS] for t in report.tables
    }
    sensitive = {t.name: {c.name for c in t.columns if c.profile.sensitive} for t in report.tables}
    raw_reports = {t.name: t for t in report.tables}
    tables = _with_missing_tables(spec, report)
    with duckdb.connect(str(out_dir / CLEAN_DATABASE)) as connection:
        connection.execute(f"ATTACH {lit(str(raw_database))} AS raw (READ_ONLY)")
        connection.execute(
            f"CREATE TABLE {QUARANTINE} (table_name VARCHAR, column_name VARCHAR, "
            "_source VARCHAR, _row BIGINT, raw_value VARCHAR)"
        )
        connection.execute(
            f"CREATE TABLE {EXCLUDED} (table_name VARCHAR, reason VARCHAR, "
            "_source VARCHAR, _row BIGINT, raw_values VARCHAR, _cell VARCHAR)"
        )
        for table in tables:
            if table.source not in raw_columns:
                continue
            try:
                result.tables.append(
                    _build_table(
                        connection,
                        table,
                        raw_columns[table.source],
                        sensitive[table.source],
                        raw_reports[table.source],
                        new_sources,
                    )
                )
            except duckdb.Error as error:
                result.tables.append(
                    TableBuild(name=table.name, source=table.source, error=str(error)[:500])
                )
        connection.execute("DETACH raw")
    return result


def _with_missing_tables(spec: DatasetSpec, report: IngestReport) -> list[TableSpec]:
    tables = list(spec.tables)
    named = {t.source for t in tables}
    taken = {t.name for t in tables}
    for raw_table in report.tables:
        if raw_table.name in named:
            continue
        name = _unique(slug(raw_table.name), taken)
        taken.add(name)
        tables.append(TableSpec(source=raw_table.name, name=name))
    return tables


def _unique(base: str, taken: set[str]) -> str:
    base = base if base[:1].isalpha() else f"t_{base}"
    name, suffix = base, 2
    while name in taken or name in META_COLUMNS:
        name, suffix = f"{base}_{suffix}", suffix + 1
    return name


def _build_table(
    connection: duckdb.DuckDBPyConnection,
    table: TableSpec,
    raw_columns: list[str],
    sensitive: set[str],
    raw_report: TableReport,
    new_sources: tuple[str, ...] = (),
) -> TableBuild:
    new = _new_condition(new_sources)
    specs = {c.source: c for c in table.columns}
    hidden = sorted(s for s in raw_columns if s in sensitive or (s in specs and specs[s].sensitive))
    taken = {c.name for c in table.columns}
    columns: list[ColumnSpec] = []
    for source in raw_columns:
        if source in hidden:
            continue
        if source not in specs:
            lowered = {t.lower() for t in taken}
            if SOURCE_NAME.fullmatch(source) and source.lower() not in lowered:
                name = source
            else:
                name = _unique(slug(source), taken)
            taken.add(name)
            specs[source] = ColumnSpec(source=source, name=name)
        columns.append(specs[source])

    stage = f"__stage_{table.name}"
    marked = f"__marked_{table.name}"
    # Exclusion sees raw values before fill_down, so a subtotal row never fills the rows below it.
    connection.execute(
        f"CREATE TEMP TABLE {q(marked)} AS SELECT *, CAST(_row AS BIGINT) AS __row, "
        f"{_exclusion_reason(table.exclude_rows)} AS __excluded FROM raw.{q(table.source)}"
    )
    proven = _exclude_proven_subtotals(connection, marked, table.source, raw_columns, raw_report)
    visible = [s for s in raw_columns if s not in hidden]
    overlaps = (
        _row_overlaps(connection, marked, raw_columns, visible, new_sources) if new_sources else []
    )
    _drop_overlaps(connection, marked, raw_columns, table.drop_overlap)
    values = ", ".join(f"{lit(s)}, {q(s)}" for s in visible)
    raw_values = f"CAST(json_object({values}) AS VARCHAR)" if visible else "'{}'"
    has_cell = any(c.name == "_cell" for c in raw_report.columns) if raw_report else False
    cell = "_cell" if has_cell else "NULL"
    connection.execute(
        f"INSERT INTO {EXCLUDED} SELECT {lit(table.name)}, __excluded, _source, __row, "
        f"{raw_values}, {cell} FROM {q(marked)} WHERE __excluded IS NOT NULL "
        "ORDER BY _source, __row"
    )
    filled = [
        (
            f"last_value({q(s)} IGNORE NULLS) OVER (PARTITION BY _source ORDER BY __row "
            f"ROWS UNBOUNDED PRECEDING) AS {q(s)}"
        )
        if s in table.fill_down
        else q(s)
        for s in raw_columns
    ]
    dedupe = ""
    if table.dedupe == "exact":
        partition = ", ".join(q(s) for s in raw_columns)
        dedupe = f"QUALIFY row_number() OVER (PARTITION BY {partition} ORDER BY _source, __row) = 1"
    connection.execute(
        f"CREATE TEMP TABLE {q(stage)} AS SELECT * FROM ("
        f"SELECT {', '.join(filled)}, _source, __row{', _cell' if has_cell else ''} FROM ("
        f"SELECT * FROM {q(marked)} WHERE __excluded IS NULL)) {dedupe}"
    )
    selected = [f"{parse_expression(c, c.source)} AS {q(c.name)}" for c in columns]
    connection.execute(
        f"CREATE TABLE {q(table.name)} AS SELECT {', '.join(selected)}, _source, __row AS _row"
        f"{', _cell' if has_cell else ''} FROM {q(stage)} ORDER BY _source, __row"
    )
    report = TableBuild(
        name=table.name,
        source=table.source,
        description=table.description,
        hidden=hidden,
        proven_subtotals=proven,
    )
    report.raw_rows = _scalar(connection, f"SELECT count(*) FROM {q(marked)}")
    common = _common_filled_count(connection, marked, raw_columns)
    full = f"{filled_count_sql(raw_columns)} >= {common}" if raw_columns else "false"
    identifiers = _identifier_columns(connection, marked, raw_columns) if table.exclude_rows else {}
    labels = _rule_labels(table.exclude_rows)
    report.excluded = _excluded_rules(connection, marked, labels, full, identifiers, "true")
    if new_sources:
        report.new_excluded = _excluded_rules(connection, marked, labels, full, identifiers, new)
        reasons = ", ".join(lit("already in " + ", ".join(d.against)) for d in table.drop_overlap)
        if reasons:
            report.new_overlap_dropped = _scalar(
                connection,
                f"SELECT count(*) FROM {q(marked)} WHERE __excluded IN ({reasons}) AND {new}",
            )
    for shape in _subtotal_shapes(raw_report):
        kept = (
            f"SELECT count(*) FROM {q(marked)} WHERE __excluded IS NULL "
            f"AND {shape_sql(raw_columns, shape.columns)}"
        )
        report.subtotal_checks.append(
            SubtotalCheck(
                columns=shape.columns,
                rows=shape.rows,
                sums_above=shape.sums_above,
                kept=_scalar(connection, kept),
            )
        )
    report.rows = _scalar(connection, f"SELECT count(*) FROM {q(stage)}")
    report.overlaps = overlaps
    names = [c.name for c in columns]
    report.duplicate_rows = _duplicate_rows(connection, table.name, names)
    if report.duplicate_rows:
        report.duplicate_key = _distinct_row_key(connection, table.name, names)
    for column in columns:
        checked = _check_column(connection, table.name, stage, column)
        if column.type == "text":
            checked.values = _known_values(connection, table.name, column.name)
        if column.type in ("date", "datetime"):
            checked.min_value, checked.max_value = _value_range(connection, table.name, column.name)
        _profile(connection, table.name, column, checked)
        report.columns.append(checked)
    if new_sources:
        report.new_rows = _scalar(connection, f"SELECT count(*) FROM {q(stage)} WHERE {new}")
        report.new_columns = [
            _check_column(connection, table.name, stage, c, scope=new, quarantine=False)
            for c in columns
        ]
        report.overlaps += _date_overlaps(connection, table.name, columns, new_sources)
    connection.execute(f"DROP TABLE {q(stage)}")
    connection.execute(f"DROP TABLE {q(marked)}")
    return report


def _excluded_rules(
    connection: duckdb.DuckDBPyConnection,
    marked: str,
    labels: list[str],
    full: str,
    identifiers: dict[str, str],
    scope: str,
) -> list[ExcludedRule]:
    """Counts per exclude rule over the rows matching scope: all rows, or the new files' rows."""
    rules = []
    for position, label in enumerate(labels, start=1):
        excluded = f"SELECT count(*) FROM {q(marked)} WHERE __excluded = {lit(label)} AND {scope}"
        rule = ExcludedRule(
            reason=label,
            position=position,
            rows=_scalar(connection, excluded),
            full_rows=_scalar(connection, f"{excluded} AND {full}"),
        )
        for column, form in identifiers.items():
            same_form = f"{signature_sql(q(column))} = {lit(form)}"
            count = _scalar(connection, f"{excluded} AND {filled_sql(column)} AND {same_form}")
            if count > rule.with_identifier:
                rule.with_identifier, rule.identifier = count, column
        rules.append(rule)
    return rules


def _file_of(source: str = "_source") -> str:
    return f"split_part({source}, '#', 1)"


def _new_condition(new_sources: Sequence[str]) -> str:
    """SQL that is true for a row of a new file."""
    if not new_sources:
        return "false"
    return f"{_file_of()} IN ({', '.join(lit(s) for s in sorted(new_sources))})"


def _rank(new_sources: Sequence[str], alias: str) -> str:
    """SQL for a row's place in upload order: 0 for a row of an older file, i for a row of the
    i-th new file. A row is compared only with rows of a lower rank."""
    branches = " ".join(f"WHEN {lit(f)} THEN {i}" for i, f in enumerate(new_sources, start=1))
    return f"CASE {_file_of(f'{alias}._source')} {branches} ELSE 0 END"


def _columns(alias: str, columns: Sequence[str], wrap: str = "{}") -> str:
    return f"list_value({', '.join(wrap.format(f'{alias}.{q(c)}') for c in columns)})"


class _Earlier:
    """Tests that a new row (alias n) repeats a kept row (alias o) that comes before it: a row of
    an older file, or of a new file earlier in upload order."""

    def __init__(self, marked: str, new_sources: Sequence[str]) -> None:
        self.marked = q(marked)
        self.rank_n, self.rank_o = _rank(new_sources, "n"), _rank(new_sources, "o")
        self.base = f"n.__excluded IS NULL AND {self.rank_n} > 0"

    def repeats(self, expression_n: str, expression_o: str, guard_n: str, guard_o: str) -> str:
        return (
            f"{guard_n} AND {expression_n} IN (SELECT {expression_o} FROM {self.marked} o "
            f"WHERE o.__excluded IS NULL AND {self.rank_o} < {self.rank_n} AND {guard_o})"
        )

    def new_files(
        self,
        connection: duckdb.DuckDBPyConnection,
        where: str,
        expression_n: str,
        expression_o: str,
        guard_o: str,
    ) -> list[str]:
        """The new files, in upload order, that hold an earlier copy of a row matching where."""
        found = connection.execute(
            f"SELECT {_file_of('o._source')} AS f, min({self.rank_o}) AS r "
            f"FROM {self.marked} n JOIN {self.marked} o ON {expression_n} = {expression_o} "
            f"AND {self.rank_o} > 0 AND {self.rank_o} < {self.rank_n} "
            f"AND o.__excluded IS NULL AND {guard_o} "
            f"WHERE {where} GROUP BY f ORDER BY r LIMIT {MAX_NAMED_FILES}"
        ).fetchall()
        return [str(row[0]) for row in found]


def _row_overlaps(
    connection: duckdb.DuckDBPyConnection,
    marked: str,
    raw_columns: list[str],
    visible: list[str],
    new_sources: Sequence[str],
) -> list[Overlap]:
    """Exact and key overlaps between kept new rows and the kept rows before them, before
    fill-down and dedupe, so a `dedupe` flag cannot hide a re-sent file."""
    found: list[Overlap] = []
    if not raw_columns:
        return found
    earlier = _Earlier(marked, new_sources)
    source = f"FROM {q(marked)} n WHERE"
    whole_n, whole_o = _columns("n", raw_columns), _columns("o", raw_columns)
    exact = earlier.repeats(whole_n, whole_o, "true", "true")
    where = f"{earlier.base} AND {exact}"
    shown = visible[:OVERLAP_EXAMPLE_COLUMNS]
    count = _scalar(connection, f"SELECT count(*) {source} {where}")
    if count:
        found.append(
            Overlap(
                kind="exact",
                rows=count,
                examples=_examples(connection, f"{source} {where}", shown, "n"),
                earlier_new_files=earlier.new_files(connection, where, whole_n, whole_o, "true"),
            )
        )
    key = _overlap_key(connection, marked, visible, _new_condition(new_sources))
    if key:
        key_n, key_o = _columns("n", key, "trim({})"), _columns("o", key, "trim({})")
        filled_n = " AND ".join(f"trim(n.{q(c)}) <> ''" for c in key)
        filled_o = " AND ".join(f"trim(o.{q(c)}) <> ''" for c in key)
        same_key = earlier.repeats(key_n, key_o, filled_n, filled_o)
        where = f"{earlier.base} AND {same_key} AND NOT ({exact})"
        count = _scalar(connection, f"SELECT count(*) {source} {where}")
        if count:
            found.append(
                Overlap(
                    kind="key",
                    rows=count,
                    key=key,
                    examples=_examples(connection, f"{source} {where}", key, "n"),
                    earlier_new_files=earlier.new_files(connection, where, key_n, key_o, filled_o),
                )
            )
    return found


def _examples(
    connection: duckdb.DuckDBPyConnection, source: str, columns: list[str], alias: str
) -> list[dict[str, str | None]]:
    rows = connection.execute(
        f"SELECT {', '.join(f'{alias}.{q(c)}' for c in columns)} {source} "
        f"ORDER BY {alias}._source, {alias}.__row LIMIT {OVERLAP_EXAMPLES}"
    ).fetchall()
    return [
        {
            c: (str(v)[:TOP_VALUE_CHARS] if v is not None else None)
            for c, v in zip(columns, row, strict=True)
        }
        for row in rows
    ]


def _overlap_key(
    connection: duckdb.DuckDBPyConnection, marked: str, columns: list[str], new: str
) -> list[str]:
    """The smallest set of 1 or 2 raw columns whose distinct non-blank values reach
    OVERLAP_KEY_SHARE of the older kept rows. Empty when there is none or nothing to compare."""
    old = f"FROM {q(marked)} WHERE __excluded IS NULL AND NOT ({new})"
    total = _scalar(connection, f"SELECT count(*) {old}")
    new_rows = _scalar(
        connection, f"SELECT count(*) FROM {q(marked)} WHERE __excluded IS NULL AND {new}"
    )
    if not total or not new_rows:
        return []
    searched = columns[:KEY_SEARCH_COLUMNS]
    for size in range(1, MAX_KEY_SIZE + 1):
        keys = list(itertools.combinations(searched, size))
        if not keys:
            continue
        counts = []
        for key in keys:
            parts = [f"trim({q(c)})" for c in key]
            filled = " AND ".join(f"{p} <> ''" for p in parts)
            counts.append(
                f"count(DISTINCT CASE WHEN {filled} THEN list_value({', '.join(parts)}) END)"
            )
        row = connection.execute(f"SELECT {', '.join(counts)} {old}").fetchone()
        for key, distinct in zip(keys, row or (), strict=False):
            if distinct >= OVERLAP_KEY_SHARE * total:
                return list(key)
    return []


def _date_overlaps(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[ColumnSpec],
    new_sources: Sequence[str],
) -> list[Overlap]:
    """New rows dated within the span of the older rows, or within the dates of a new file
    earlier in upload order, per date column of the clean table. Each earlier new file counts by
    its own dates, so November sent after December is no overlap."""
    found = []
    rank_n, rank_w = _rank(new_sources, "n"), _rank(new_sources, "w")
    for column in columns:
        if column.type not in ("date", "datetime"):
            continue
        name = q(column.name)
        spans = (
            f"(SELECT {_file_of('w._source')} AS f, min({rank_w}) AS r, min(w.{name}) AS lo, "
            f"max(w.{name}) AS hi FROM {q(table)} w WHERE {rank_w} > 0 GROUP BY f)"
        )
        in_new = f"s.r < {rank_n} AND n.{name} BETWEEN s.lo AND s.hi"
        old = f"FROM {q(table)} o WHERE {_rank(new_sources, 'o')} = 0"
        where = (
            f"{rank_n} > 0 AND (n.{name} BETWEEN (SELECT min(o.{name}) {old}) "
            f"AND (SELECT max(o.{name}) {old}) OR EXISTS (SELECT 1 FROM {spans} s WHERE {in_new}))"
        )
        inside = f"FROM {q(table)} n WHERE {where}"
        count = _scalar(connection, f"SELECT count(*) {inside}")
        if count:
            rows = connection.execute(
                f"SELECT CAST(n.{name} AS VARCHAR) {inside} ORDER BY n.{name}, n._source, n._row "
                f"LIMIT {OVERLAP_EXAMPLES}"
            ).fetchall()
            examples: list[dict[str, str | None]] = [{column.name: str(r[0])} for r in rows]
            files = connection.execute(
                f"SELECT s.f FROM {spans} s WHERE EXISTS "
                f"(SELECT 1 {inside} AND {in_new}) ORDER BY s.r LIMIT {MAX_NAMED_FILES}"
            ).fetchall()
            found.append(
                Overlap(
                    kind="date",
                    rows=count,
                    column=column.name,
                    examples=examples,
                    earlier_new_files=[str(f[0]) for f in files],
                )
            )
    return found


def _drop_overlaps(
    connection: duckdb.DuckDBPyConnection,
    marked: str,
    raw_columns: list[str],
    drops: list[OverlapDrop],
) -> None:
    """Mark the rows of each drop's file that repeat a kept row of its `against` files, by its
    key columns (not blank) or by whole row, so they land in `_excluded` with their file."""
    for drop in drops:
        if drop.key:
            match = " AND ".join(
                f"trim(n.{q(k)}) = trim(o.{q(k)}) AND trim(n.{q(k)}) <> ''" for k in drop.key
            )
        else:
            match = " AND ".join(f"n.{q(c)} IS NOT DISTINCT FROM o.{q(c)}" for c in raw_columns)
        if not match:
            continue
        against = ", ".join(lit(f) for f in drop.against)
        connection.execute(
            f"UPDATE {q(marked)} SET __excluded = {lit('already in ' + ', '.join(drop.against))} "
            f"FROM (SELECT DISTINCT n._source AS s, n.__row AS r FROM {q(marked)} n "
            f"JOIN {q(marked)} o ON {match} AND NOT (n._source = o._source AND n.__row = o.__row) "
            f"WHERE n.__excluded IS NULL AND o.__excluded IS NULL "
            f"AND {_file_of('n._source')} = {lit(drop.file)} "
            f"AND {_file_of('o._source')} IN ({against})) AS p "
            f"WHERE {q(marked)}._source = p.s AND {q(marked)}.__row = p.r "
            f"AND {q(marked)}.__excluded IS NULL"
        )


def _subtotal_shapes(raw_report: TableReport) -> list[RowShape]:
    """Rare row shapes whose rows mostly equal the sum of the rows above them."""
    return [
        shape
        for shape in raw_report.rare_shapes
        if shape.sums_above >= 2 and shape.sums_above >= SUBTOTAL_SHARE * shape.rows
    ]


def _exclude_proven_subtotals(
    connection: duckdb.DuckDBPyConnection,
    marked: str,
    source: str,
    raw_columns: list[str],
    raw_report: TableReport,
) -> int:
    """Mark the rows of subtotal shapes that each equal the sum of the records above them. Rows a
    spec rule already excludes keep that rule's reason. Returns how many rows code marked."""
    every = [shape.columns for shape in raw_report.rare_shapes]
    open_count = f"SELECT count(*) FROM {q(marked)} WHERE __excluded IS NULL"
    start = _scalar(connection, open_count)
    # Each round proves one level of totals: days, then months over the days, then years. A group
    # ends at any row of the shape (a day's total, whichever rule removed its neighbours), or only
    # at rows still in the table (a month's total over days already removed).
    for _ in range(PROVEN_SUBTOTAL_ROUNDS):
        before = _scalar(connection, open_count)
        for shape, open_rows in itertools.product(
            _subtotal_shapes(raw_report), ("true", "__excluded IS NULL")
        ):
            proof = subtotal_rows_sql(
                q(marked), raw_columns, shape.columns, every, PROVEN_SUBTOTAL_MIN_ROWS,
                open_rows=open_rows,
            )  # fmt: skip
            connection.execute(
                f"UPDATE {q(marked)} SET __excluded = {lit(PROVEN_SUBTOTAL)} FROM ({proof}) AS p "
                f"WHERE {q(marked)}._source = p._source AND {q(marked)}.__row = p.r "
                f"AND {q(marked)}.__excluded IS NULL"
            )
        if _scalar(connection, open_count) == before:
            break
    return start - _scalar(connection, open_count)


def _duplicate_rows(connection: duckdb.DuckDBPyConnection, table: str, columns: list[str]) -> int:
    if not columns:
        return 0
    listed = ", ".join(q(c) for c in columns)
    return _scalar(
        connection,
        f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT {listed} FROM {q(table)})) "
        f"FROM {q(table)}",
    )


def _distinct_row_key(
    connection: duckdb.DuckDBPyConnection, table: str, columns: list[str]
) -> list[str]:
    """The smallest set of columns, fewer than all, with no blanks that tells every distinct row
    apart, such as an order id and a line number. Empty when there is none."""
    listed = ", ".join(q(c) for c in columns)
    distinct = "__distinct_rows"
    connection.execute(f"CREATE TEMP TABLE {distinct} AS SELECT DISTINCT {listed} FROM {q(table)}")
    try:
        total = _scalar(connection, f"SELECT count(*) FROM {distinct}")
        searched = columns[:KEY_SEARCH_COLUMNS]
        for size in range(1, min(MAX_KEY_SIZE, len(columns) - 1) + 1):
            for key in itertools.combinations(searched, size):
                filled = " AND ".join(f"{q(c)} IS NOT NULL" for c in key)
                parts = ", ".join(q(c) for c in key)
                count = _scalar(
                    connection,
                    f"SELECT count(*) FROM (SELECT DISTINCT {parts} FROM {distinct} "
                    f"WHERE {filled})",
                )
                if count == total:
                    return list(key)
        return []
    finally:
        connection.execute(f"DROP TABLE {distinct}")


def _value_range(
    connection: duckdb.DuckDBPyConnection, table: str, column: str
) -> tuple[str | None, str | None]:
    """The earliest and latest value, so the agent and verification know the period covered."""
    row = connection.execute(
        f"SELECT min({q(column)}), max({q(column)}) FROM {q(table)}"
    ).fetchone()
    if row is None:
        return None, None
    return (
        str(row[0]) if row[0] is not None else None,
        str(row[1]) if row[1] is not None else None,
    )


def _profile(
    connection: duckdb.DuckDBPyConnection, table: str, column: ColumnSpec, report: ColumnBuild
) -> None:
    """Distinct count, number range and mean, and the most frequent text or boolean values."""
    name = q(column.name)
    filled = f"{name} IS NOT NULL" + (f" AND trim({name}) <> ''" if column.type == "text" else "")
    report.distinct = _scalar(
        connection, f"SELECT count(DISTINCT {name}) FROM {q(table)} WHERE {filled}"
    )
    if column.type in NUMBER_TYPES:
        row = connection.execute(
            f"SELECT min({name}), max({name}), avg({name}) FROM {q(table)}"
        ).fetchone()
        if row is not None:
            report.number_min, report.number_max, report.number_mean = (
                float(value) if value is not None else None for value in row
            )
    if column.type in ("text", "boolean"):
        rows = connection.execute(
            f"SELECT CAST({name} AS VARCHAR), count(*) AS n FROM {q(table)} WHERE {filled} "
            f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {TOP_VALUES}"
        ).fetchall()
        report.top_values = [
            ValueCount(value=str(value)[:TOP_VALUE_CHARS], rows=n) for value, n in rows
        ]


def _known_values(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> list[str]:
    """The distinct values of a text column, most frequent first, when there are few and short."""
    rows = connection.execute(
        f"SELECT {q(column)}, count(*) AS n FROM {q(table)} "
        f"WHERE {q(column)} IS NOT NULL AND trim({q(column)}) <> '' "
        f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {MAX_KNOWN_VALUES + 1}"
    ).fetchall()
    values = [str(value) for value, _ in rows]
    if len(values) > MAX_KNOWN_VALUES or any(len(v) > MAX_KNOWN_VALUE_CHARS for v in values):
        return []
    return values


def _identifier_columns(
    connection: duckdb.DuckDBPyConnection, table: str, columns: list[str]
) -> dict[str, str]:
    """Columns that identify kept rows, such as an order id, with the common form of their values.
    See ID_FILLED and the others."""
    kept = _scalar(connection, f"SELECT count(*) FROM {q(table)} WHERE __excluded IS NULL")
    if kept < ID_MIN_ROWS or not columns:
        return {}
    parts = []
    for column in columns:
        value = f"NULLIF(trim({q(column)}), '')"
        parts += [f"count({value})", f"count(DISTINCT {value})", f"count({number_sql(value)})"]
    row = connection.execute(
        f"SELECT {', '.join(parts)} FROM {q(table)} WHERE __excluded IS NULL"
    ).fetchone()
    if row is None:
        return {}
    found: dict[str, str] = {}
    for position, column in enumerate(columns):
        filled, distinct, numeric = row[3 * position : 3 * position + 3]
        if (
            filled >= ID_FILLED * kept
            and distinct >= ID_DISTINCT * filled
            and numeric < ID_NUMERIC * filled
        ):
            form = connection.execute(
                f"SELECT {signature_sql(q(column))} AS form FROM {q(table)} "
                f"WHERE __excluded IS NULL AND {filled_sql(column)} "
                "GROUP BY form ORDER BY count(*) DESC LIMIT 1"
            ).fetchone()
            if form:
                found[column] = str(form[0])
    return found


def _common_filled_count(
    connection: duckdb.DuckDBPyConnection, table: str, columns: list[str]
) -> int:
    """Filled cells in the table's most common row shape."""
    if not columns:
        return 0
    flags = ", ".join(filled_sql(c) for c in columns)
    row = connection.execute(
        f"SELECT shape FROM (SELECT list_value({flags}) AS shape, count(*) AS n FROM {q(table)} "
        "GROUP BY shape) ORDER BY n DESC LIMIT 1"
    ).fetchone()
    return sum(row[0]) if row else 0


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _check_column(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    stage: str,
    column: ColumnSpec,
    scope: str = "true",
    quarantine: bool = True,
) -> ColumnBuild:
    """Parse stats over the stage rows matching scope. Only the whole-table check writes the
    quarantine, so a second pass over the new rows does not repeat its rows."""
    report = ColumnBuild(name=column.name, source=column.source, type=column.type)
    raw = q(column.source)
    non_blank = f"{scope} AND {raw} IS NOT NULL AND trim({raw}) <> ''"
    report.non_blank = _scalar(connection, f"SELECT count(*) FROM {q(stage)} WHERE {non_blank}")
    if column.type == "text":
        report.parsed = report.non_blank
        return report
    failed = f"{non_blank} AND ({parse_expression(column, column.source)}) IS NULL"
    if quarantine:
        connection.execute(
            f"INSERT INTO {QUARANTINE} SELECT {lit(table)}, {lit(column.name)}, _source, __row, "
            f"{raw} FROM {q(stage)} WHERE {failed}"
        )
    failures = _scalar(connection, f"SELECT count(*) FROM {q(stage)} WHERE {failed}")
    report.parsed = report.non_blank - failures
    samples = connection.execute(
        f"SELECT {raw}, count(*) AS n FROM {q(stage)} WHERE {failed} "
        f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {FAILED_SAMPLES}"
    ).fetchall()
    report.failed_samples = [str(value)[:60] for value, _ in samples]
    return report
