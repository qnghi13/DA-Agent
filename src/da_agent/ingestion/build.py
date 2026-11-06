# ruff: noqa: S608 - this module compiles SQL on purpose. Identifiers go through q() and literals
# through lit(), and tests/unit/test_dataset_build.py covers hostile names and values.
"""Build clean tables from raw tables and a DatasetSpec. Runs inside the sandbox.

Every identifier is quoted and every literal escaped, and only values from the spec's fixed choices
reach the SQL, so a spec cannot inject SQL. Values that fail to parse become NULL and are copied to
`_quarantine` with their source and row, and rows that exclude rules remove, such as subtotals, are
copied to `_excluded`, so nothing is dropped silently.
"""

from pathlib import Path

import duckdb
from pydantic import BaseModel, Field

from da_agent.ingestion.raw import slug
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import (
    META_COLUMNS,
    ColumnSpec,
    DatasetSpec,
    RowCondition,
    RowRule,
    TableSpec,
)

CLEAN_DATABASE = "dataset.duckdb"
QUARANTINE = "_quarantine"
EXCLUDED = "_excluded"
FAILED_SAMPLES = 5
DEFAULT_TRUE = ("true", "1", "yes", "y", "x", "có", "co", "đúng", "dung", "rồi")
DEFAULT_FALSE = ("false", "0", "no", "n", "không", "khong", "sai", "chưa")
ISO_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
DATE_FORMATS = {
    "dmy": ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"),
    "mdy": ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m-%d-%Y"),
    "ymd": ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d"),
}
VI_TEXT_DATE = r"^(\d{1,2})\s+(thg|tháng)\s*(\d{1,2}),?\s+(\d{4})$"
# Excel serial numbers from 1954 to 2119. Five-digit numbers outside this range stay unparsed.
EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX = 20000, 80000


class ColumnBuild(BaseModel):
    name: str
    source: str
    type: str
    non_blank: int = 0
    parsed: int = 0
    failed_samples: list[str] = Field(default_factory=list)

    @property
    def parse_rate(self) -> float:
        return self.parsed / self.non_blank if self.non_blank else 1.0


class ExcludedRule(BaseModel):
    reason: str
    rows: int = 0


class TableBuild(BaseModel):
    name: str
    source: str
    raw_rows: int = 0
    rows: int = 0
    columns: list[ColumnBuild] = Field(default_factory=list)
    hidden: list[str] = Field(default_factory=list)
    excluded: list[ExcludedRule] = Field(default_factory=list)
    error: str | None = None


class BuildReport(BaseModel):
    tables: list[TableBuild] = Field(default_factory=list)


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


SHORTHAND = r"^(\d+)(tr|triệu|k)(\d{1,3})$"


def _number(text: str) -> str:
    """SQL for a DOUBLE from local number text: 22,000 / 1.350.000 / 25000k / 578.000đ / 1.5tr.

    Without a suffix, groups of three digits mean thousands whatever the separator, so 22,000 can
    never become 22, and one separator without three digits after it is a decimal point: 2,5.
    Before a suffix, one separator is always a decimal point: 151,036tr is 151.036 million.
    Shorthand such as 1tr270 (1,270,000) and 1k5 (1,500) is read too.
    """
    compact = f"regexp_replace(lower(trim({text})), '\\s+', '', 'g')"
    bare = f"regexp_replace({compact}, '(vnđ|vnd|đồng|đ)$', '')"
    multiplier = (
        f"CASE WHEN regexp_matches({bare}, '(k|nghìn|ngàn)$') THEN 1000 "
        f"WHEN regexp_matches({bare}, '(tr|triệu)$') THEN 1000000 ELSE 1 END"
    )
    digits = f"regexp_replace({bare}, '(k|nghìn|ngàn|tr|triệu)$', '')"
    # With a suffix, one separator is a decimal point: 151,036tr is 151.036 million.
    suffixed = f"({multiplier}) > 1 AND regexp_matches({digits}, '^-?\\d+[.,]\\d+$')"
    normal = (
        f"CASE WHEN {suffixed} THEN replace({digits}, ',', '.') "
        f"WHEN regexp_matches({digits}, '^-?\\d{{1,3}}(,\\d{{3}})+(\\.\\d+)?$') "
        f"THEN replace({digits}, ',', '') "
        f"WHEN regexp_matches({digits}, '^-?\\d{{1,3}}(\\.\\d{{3}})+(,\\d+)?$') "
        f"THEN replace(replace({digits}, '.', ''), ',', '.') "
        f"WHEN regexp_matches({digits}, '^-?\\d+[.,]\\d+$') THEN replace({digits}, ',', '.') "
        f"ELSE {digits} END"
    )
    major = f"TRY_CAST(regexp_extract({bare}, {lit(SHORTHAND)}, 1) AS DOUBLE)"
    unit = f"regexp_extract({bare}, {lit(SHORTHAND)}, 2)"
    minor = f"TRY_CAST(rpad(regexp_extract({bare}, {lit(SHORTHAND)}, 3), 3, '0') AS DOUBLE)"
    shorthand = (
        f"CASE WHEN {unit} = 'k' THEN {major} * 1000 + {minor} "
        f"ELSE {major} * 1000000 + {minor} * 1000 END"
    )
    return (
        f"(CASE WHEN regexp_matches({bare}, {lit(SHORTHAND)}) THEN {shorthand} "
        f"ELSE TRY_CAST({normal} AS DOUBLE) * {multiplier} END)"
    )


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
        return _number(text)
    if kind in ("integer", "money"):
        number = _number(text)
        if kind == "money":
            return f"CAST(round({number}) AS BIGINT)"
        return f"CASE WHEN {number} = floor({number}) THEN CAST({number} AS BIGINT) END"
    if kind == "quantity":
        leading = f"regexp_extract(trim({text}), '^(-?\\d+(?:[.,]\\d+)?)', 1)"
        return _number(f"NULLIF({leading}, '')")
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
    raw_database: Path, spec: DatasetSpec, report: IngestReport, out_dir: Path
) -> BuildReport:
    """Write dataset.duckdb to out_dir. One failing table is reported and the rest still build."""
    result = BuildReport()
    raw_columns = {
        t.name: [c.name for c in t.columns if c.name not in META_COLUMNS] for t in report.tables
    }
    sensitive = {t.name: {c.name for c in t.columns if c.profile.sensitive} for t in report.tables}
    tables = _with_missing_tables(spec, report)
    with duckdb.connect(str(out_dir / CLEAN_DATABASE)) as connection:
        connection.execute(f"ATTACH {lit(str(raw_database))} AS raw (READ_ONLY)")
        connection.execute(
            f"CREATE TABLE {QUARANTINE} (table_name VARCHAR, column_name VARCHAR, "
            "_source VARCHAR, _row BIGINT, raw_value VARCHAR)"
        )
        connection.execute(
            f"CREATE TABLE {EXCLUDED} (table_name VARCHAR, reason VARCHAR, "
            "_source VARCHAR, _row BIGINT, raw_values VARCHAR)"
        )
        for table in tables:
            if table.source not in raw_columns:
                continue
            try:
                result.tables.append(
                    _build_table(
                        connection, table, raw_columns[table.source], sensitive[table.source]
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
) -> TableBuild:
    specs = {c.source: c for c in table.columns}
    hidden = sorted(s for s in raw_columns if s in sensitive or (s in specs and specs[s].sensitive))
    taken = {c.name for c in table.columns}
    columns: list[ColumnSpec] = []
    for source in raw_columns:
        if source in hidden:
            continue
        if source not in specs:
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
    visible = [s for s in raw_columns if s not in hidden]
    values = ", ".join(f"{lit(s)}, {q(s)}" for s in visible)
    raw_values = f"CAST(json_object({values}) AS VARCHAR)" if visible else "'{}'"
    connection.execute(
        f"INSERT INTO {EXCLUDED} SELECT {lit(table.name)}, __excluded, _source, __row, "
        f"{raw_values} FROM {q(marked)} WHERE __excluded IS NOT NULL ORDER BY _source, __row"
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
        f"SELECT {', '.join(filled)}, _source, __row FROM ("
        f"SELECT * FROM {q(marked)} WHERE __excluded IS NULL)) {dedupe}"
    )
    selected = [f"{parse_expression(c, c.source)} AS {q(c.name)}" for c in columns]
    connection.execute(
        f"CREATE TABLE {q(table.name)} AS SELECT {', '.join(selected)}, _source, __row AS _row "
        f"FROM {q(stage)} ORDER BY _source, __row"
    )
    report = TableBuild(name=table.name, source=table.source, hidden=hidden)
    report.raw_rows = _scalar(connection, f"SELECT count(*) FROM {q(marked)}")
    for label in _rule_labels(table.exclude_rows):
        excluded = f"SELECT count(*) FROM {q(marked)} WHERE __excluded = {lit(label)}"
        report.excluded.append(ExcludedRule(reason=label, rows=_scalar(connection, excluded)))
    report.rows = _scalar(connection, f"SELECT count(*) FROM {q(stage)}")
    for column in columns:
        report.columns.append(_check_column(connection, table.name, stage, column))
    connection.execute(f"DROP TABLE {q(stage)}")
    connection.execute(f"DROP TABLE {q(marked)}")
    return report


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _check_column(
    connection: duckdb.DuckDBPyConnection, table: str, stage: str, column: ColumnSpec
) -> ColumnBuild:
    report = ColumnBuild(name=column.name, source=column.source, type=column.type)
    raw = q(column.source)
    non_blank = f"{raw} IS NOT NULL AND trim({raw}) <> ''"
    report.non_blank = _scalar(connection, f"SELECT count(*) FROM {q(stage)} WHERE {non_blank}")
    if column.type == "text":
        report.parsed = report.non_blank
        return report
    failed = f"{non_blank} AND ({parse_expression(column, column.source)}) IS NULL"
    connection.execute(
        f"INSERT INTO {QUARANTINE} SELECT {lit(table)}, {lit(column.name)}, _source, __row, {raw} "
        f"FROM {q(stage)} WHERE {failed}"
    )
    failures = _scalar(connection, f"SELECT count(*) FROM {q(stage)} WHERE {failed}")
    report.parsed = report.non_blank - failures
    samples = connection.execute(
        f"SELECT {raw}, count(*) AS n FROM {q(stage)} WHERE {failed} "
        f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {FAILED_SAMPLES}"
    ).fetchall()
    report.failed_samples = [str(value)[:60] for value, _ in samples]
    return report
