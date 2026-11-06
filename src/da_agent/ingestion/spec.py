"""DatasetSpec: a declarative description of how to clean raw tables. See docs/adr/0006.

A spec is data. The build stage compiles it into SQL with quoted identifiers and escaped literals,
so a manipulated proposal cannot run code. Columns that the raw profile marked sensitive stay
hidden whatever the spec says.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from da_agent.ingestion.report import IngestReport

Identifier = str
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
META_COLUMNS = ("_source", "_row")

ColumnType = Literal[
    "text", "integer", "number", "money", "quantity", "date", "datetime", "boolean"
]
DateFormat = Literal["dmy", "mdy", "ymd", "dm", "excel_serial", "vi_text"]
DATE_FORMAT_NAMES = ("dmy", "mdy", "ymd", "dm", "excel_serial", "vi_text")
# Pattern names from the raw profile, which models tend to copy into date_formats.
PATTERN_FORMATS = {"date_dmy": "dmy", "date_text_vi": "vi_text"}
ISO_PATTERNS = {"date_iso", "datetime_iso", "iso"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(_Model):
    source: str = Field(description="Raw column name, exactly as in the profile.")
    name: Identifier = Field(pattern=IDENTIFIER_PATTERN, description="Clean snake_case name.")
    type: ColumnType = "text"
    date_formats: list[DateFormat] = Field(default_factory=list, max_length=5)
    true_values: list[str] = Field(default_factory=list, max_length=20)
    sensitive: bool = False
    description: str = Field(default="", max_length=300)


class TableSpec(_Model):
    source: str = Field(description="Raw table name, exactly as in the profile.")
    name: Identifier = Field(pattern=IDENTIFIER_PATTERN)
    description: str = Field(default="", max_length=500)
    columns: list[ColumnSpec] = Field(default_factory=list, max_length=300)
    fill_down: list[str] = Field(
        default_factory=list,
        description="Raw columns whose blank cells repeat the value above, within one source.",
    )
    dedupe: Literal["none", "exact"] = "none"


class Relationship(_Model):
    from_table: Identifier = Field(pattern=IDENTIFIER_PATTERN)
    from_column: Identifier = Field(pattern=IDENTIFIER_PATTERN)
    to_table: Identifier = Field(pattern=IDENTIFIER_PATTERN)
    to_column: Identifier = Field(pattern=IDENTIFIER_PATTERN)


class DatasetSpec(_Model):
    tables: list[TableSpec] = Field(max_length=200)
    relationships: list[Relationship] = Field(default_factory=list, max_length=200)
    notes: list[str] = Field(default_factory=list, max_length=50)


def check_spec(spec: DatasetSpec, report: IngestReport) -> list[str]:
    """Problems a model can fix: unknown sources, duplicate names, dangling relationships."""
    problems: list[str] = []
    raw = {t.name: {c.name for c in t.columns} - set(META_COLUMNS) for t in report.tables}
    clean: dict[str, set[str]] = {}
    for table in spec.tables:
        if table.source not in raw:
            problems.append(f"table {table.source!r} is not in the raw profile")
            continue
        if table.name in clean:
            problems.append(f"table name {table.name!r} is used twice")
        names: set[str] = set(META_COLUMNS)
        for column in table.columns:
            if column.source not in raw[table.source]:
                problems.append(f"{table.source}: column {column.source!r} is not in the raw table")
            if column.name in names:
                problems.append(f"{table.name}: column name {column.name!r} is used twice")
            names.add(column.name)
            if {"dmy", "mdy"} <= set(column.date_formats):
                problems.append(
                    f"{table.name}.{column.name}: both dmy and mdy make 01/02/2025 ambiguous; "
                    "choose the one the samples show"
                )
        for source in table.fill_down:
            if source not in raw[table.source]:
                problems.append(f"{table.source}: fill_down column {source!r} is not in the table")
        clean[table.name] = names
    for link in spec.relationships:
        for table, column in ((link.from_table, link.from_column), (link.to_table, link.to_column)):
            if column not in clean.get(table, set()):
                problems.append(f"relationship refers to unknown column {table}.{column}")
    return problems


def _first_error(error: ValidationError) -> str:
    detail = error.errors()[0]
    where = ".".join(str(part) for part in detail["loc"])
    return f"{where}: {detail['msg']}"


def parse_spec_lenient(data: object) -> tuple[DatasetSpec, list[str]]:
    """Keep every valid table, column, and relationship, and list what was dropped and why.

    One bad field then costs one column, not the whole proposal, and the repair loop fixes the rest.
    """
    if not isinstance(data, dict):
        return DatasetSpec(tables=[]), ["the reply must be a JSON object with a 'tables' list"]
    problems: list[str] = []
    tables: list[TableSpec] = []
    for position, raw_table in enumerate(data.get("tables") or [], start=1):
        if not isinstance(raw_table, dict):
            problems.append(f"table {position} is not an object")
            continue
        columns: list[ColumnSpec] = []
        for raw_column in raw_table.get("columns") or []:
            if isinstance(raw_column, dict) and isinstance(raw_column.get("date_formats"), list):
                # Models often copy the profile's pattern names. ISO needs no format.
                formats = [
                    PATTERN_FORMATS.get(f, f)
                    for f in raw_column["date_formats"]
                    if f not in ISO_PATTERNS
                ]
                raw_column = {**raw_column, "date_formats": formats}
                unknown = [f for f in formats if f not in DATE_FORMAT_NAMES]
                if unknown:
                    problems.append(
                        f"{raw_table.get('name')} column {raw_column.get('source')!r}: date "
                        f"formats {unknown} ignored; ISO dates are always accepted"
                    )
                    raw_column = {
                        **raw_column,
                        "date_formats": [f for f in formats if f in DATE_FORMAT_NAMES],
                    }
            try:
                columns.append(ColumnSpec.model_validate(raw_column))
            except ValidationError as error:
                source = raw_column.get("source") if isinstance(raw_column, dict) else raw_column
                label = raw_table.get("name") or raw_table.get("source")
                problems.append(f"{label} column {source!r} dropped: {_first_error(error)}")
        try:
            tables.append(TableSpec.model_validate({**raw_table, "columns": columns}))
        except ValidationError as error:
            problems.append(f"table {raw_table.get('source')!r} dropped: {_first_error(error)}")
    relationships: list[Relationship] = []
    for position, raw_link in enumerate(data.get("relationships") or [], start=1):
        try:
            relationships.append(Relationship.model_validate(raw_link))
        except ValidationError as error:
            problems.append(f"relationship {position} dropped: {_first_error(error)}")
    notes = [str(note)[:500] for note in (data.get("notes") or [])][:50]
    return DatasetSpec(tables=tables, relationships=relationships, notes=notes), problems


def _unique_name(base: str, taken: set[str]) -> str:
    name, suffix = base, 2
    while name in taken:
        name, suffix = f"{base[:58]}_{suffix}", suffix + 1
    return name


def sanitize_spec(spec: DatasetSpec, report: IngestReport) -> tuple[DatasetSpec, list[str]]:
    """Drop or rename what check_spec would reject, so the rest of the spec can still be built.

    Sources given as a file label, such as "Orders.xlsx#Sheet1", map to the table holding it.
    """
    problems: list[str] = []
    raw = {t.name: {c.name for c in t.columns} - set(META_COLUMNS) for t in report.tables}
    by_label = {s.source: t.name for t in report.tables for s in t.sources}
    by_label.update({s.source.split("#", 1)[0]: t.name for t in report.tables for s in t.sources})
    tables: list[TableSpec] = []
    table_names: set[str] = set()
    for table in spec.tables:
        source = table.source if table.source in raw else by_label.get(table.source)
        if source is None:
            problems.append(f"table {table.source!r} is not in the raw profile; dropped")
            continue
        names: set[str] = set(META_COLUMNS)
        columns = []
        for column in table.columns:
            if column.source not in raw[source]:
                problems.append(f"{source}: column {column.source!r} is not in the table; dropped")
                continue
            name = _unique_name(column.name, names)
            if name != column.name:
                problems.append(f"{table.name}: column name {column.name!r} used twice; renamed")
            formats = list(column.date_formats)
            if {"dmy", "mdy"} <= set(formats):
                later = "mdy" if formats.index("dmy") < formats.index("mdy") else "dmy"
                formats.remove(later)
                problems.append(
                    f"{table.name}.{name}: dmy and mdy together are ambiguous; kept the first"
                )
            names.add(name)
            columns.append(column.model_copy(update={"name": name, "date_formats": formats}))
        fill_down = [c for c in table.fill_down if c in raw[source]]
        for missing in sorted(set(table.fill_down) - set(fill_down)):
            problems.append(f"{source}: fill_down column {missing!r} is not in the table; dropped")
        name = _unique_name(table.name, table_names)
        table_names.add(name)
        tables.append(
            table.model_copy(
                update={"source": source, "name": name, "columns": columns, "fill_down": fill_down}
            )
        )
    clean = {t.name: {c.name for c in t.columns} | set(META_COLUMNS) for t in tables}
    relationships = []
    for link in spec.relationships:
        ends = ((link.from_table, link.from_column), (link.to_table, link.to_column))
        missing = [f"{t}.{c}" for t, c in ends if c not in clean.get(t, set())]
        if missing:
            problems.append(f"relationship refers to unknown column {missing[0]}; dropped")
        else:
            relationships.append(link)
    sanitized = spec.model_copy(update={"tables": tables, "relationships": relationships})
    return sanitized, problems
