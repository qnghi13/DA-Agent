"""Raw ingestion: turn an upload directory into text tables in DuckDB, plus a report.

Runs inside the sandbox (docs/adr/0009). It never interprets values: types, fill-down, and cleaning
belong to the DatasetSpec stage. Every row keeps `_source` and `_row`, so any value can be traced
back to its file, sheet, and row.
"""

import csv
import math
import os
import re
import shutil
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from da_agent.ingestion.grid import Grid, plain
from da_agent.ingestion.layout_apply import TidyRow, apply_sheet
from da_agent.ingestion.layout_findings import SheetLayout, describe_sheet, find_families
from da_agent.ingestion.layout_spec import LayoutSpec
from da_agent.ingestion.limits import IngestLimits
from da_agent.ingestion.profile import SAMPLE_CHARS, SENSITIVE_MARK, ColumnStats, classify
from da_agent.ingestion.readers import ReadError, Row, Sheet, read_csv, read_sqlite, read_xlsx
from da_agent.ingestion.report import (
    ColumnProfile,
    ColumnReport,
    FileReport,
    IngestReport,
    RowShape,
    SourceReport,
    TableReport,
)
from da_agent.ingestion.sqltext import sums_above_sql

RAW_DATABASE = "raw.duckdb"
SOURCE_COLUMN, ROW_COLUMN, CELL_COLUMN = "_source", "_row", "_cell"
FAST_META = (SOURCE_COLUMN, ROW_COLUMN)
LAYOUT_META = (SOURCE_COLUMN, ROW_COLUMN, CELL_COLUMN)
TABLE_FILES = {".csv": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".xlsm": "xlsx"}
SQLITE_FILES = {".sqlite", ".sqlite3", ".db"}
DOCUMENT_FILES = {".md", ".txt"}
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_HEADER_TEXT_SHARE = 0.8
_BLANK_ROW_LIMIT = 1000
# Work files end rows with a bare newline, and DuckDB is told so: when it guesses, a line break
# inside a quoted cell can make it pick the wrong one and reject the file.
_LINE_END = "\n"
# Row shapes rarer than this, with fewer filled cells than the common shape, are reported. They are
# often subtotal or note rows that the DatasetSpec should exclude.
RARE_SHAPE_SHARE = 0.05
MAX_RARE_SHAPES = 5
SHAPE_EXAMPLES = 2


@dataclass
class _SheetResult:
    source: str
    path: Path
    header: list[str]
    width: int
    rows: int
    header_row: int
    skipped_rows: int
    truncated: bool
    long_cells: int


@dataclass
class _Group:
    name: str
    header: list[str]
    sheets: list[_SheetResult] = field(default_factory=list)


def ingest(
    input_dir: Path,
    out_dir: Path,
    limits: IngestLimits | None = None,
    layout: LayoutSpec | None = None,
) -> IngestReport:
    """Read every file into raw tables. Families of sheets that `layout` describes are made by
    applying it; every other sheet takes the fast path (one header row, then records)."""
    limits = limits or IngestLimits()
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    report = IngestReport()
    groups: dict[tuple[str, ...], _Group] = {}
    names: set[str] = set()
    layouts: list[SheetLayout] = []
    files_read: list[tuple[Path, str, list[_SheetResult]]] = []

    files = _list_files(input_dir, report)
    if len(files) > limits.max_files:
        report.warnings.append(f"only the first {limits.max_files} of {len(files)} files are read")
        files = files[: limits.max_files]

    for index, path in enumerate(files):
        relative = path.relative_to(input_dir).as_posix()
        file_report = FileReport(path=relative, status="loaded")
        report.files.append(file_report)
        kind = _kind(path)
        if kind == "junk":
            file_report.status, file_report.reason = "ignored", "system or lock file"
            continue
        if kind is None:
            file_report.status, file_report.reason = "unsupported", "unsupported file type"
            continue
        problem = _safety_problem(path, kind, limits)
        if problem:
            file_report.status, file_report.reason = "rejected", problem
            continue
        if kind == "document":
            file_report.status = "document"
            text = path.read_text(encoding="utf-8", errors="replace")[: limits.max_document_chars]
            report.documents.append({"path": relative, "text": text})
            continue
        try:
            read = list(_read_file(path, kind, file_report, limits, work / f"f{index}"))
        except (ReadError, OSError, ValueError, csv.Error) as error:
            file_report.status, file_report.reason = "error", str(error)[:300]
            continue
        layouts += [found for _, found in read if found is not None]
        files_read.append((path, kind, [sheet for sheet, _ in read if sheet is not None]))
    report.layout = find_families(list(layouts), limits.preview_cells)
    made: list[_LayoutTable] = []
    if layout is not None and layout.families:
        made = _apply_layouts(report, layout, {p.name: (p, k) for p, k, _ in files_read}, limits)
    covered = {source for table in made for source in table.sources}
    for path, kind, sheets in files_read:
        kept = [sheet for sheet in sheets if sheet.source not in covered]
        _register(kept, path, kind, groups, names)

    with duckdb.connect(str(out_dir / RAW_DATABASE)) as connection:
        for group in groups.values():
            # One table that will not load must not cost the user every other table.
            try:
                report.tables.append(_load_group(connection, group, work))
            except duckdb.Error as error:
                connection.execute(f"DROP TABLE IF EXISTS {_quote(group.name)}")
                sources = ", ".join(sheet.source for sheet in group.sheets[:5])
                more = f" and {len(group.sheets) - 5} more" if len(group.sheets) > 5 else ""
                first_line = (
                    str(error).splitlines()[0][:300] if str(error) else type(error).__name__
                )
                report.warnings.append(
                    f"table {group.name!r} from {sources}{more} could not be loaded: {first_line}"
                )
        for table in made:
            table.name = _unique(table.name, names)
            names.add(table.name)
            try:
                report.tables.append(_load_layout_table(connection, table, work))
            except duckdb.Error as error:
                connection.execute(f"DROP TABLE IF EXISTS {_quote(table.name)}")
                first_line = str(error).splitlines()[0][:300] if str(error) else "error"
                report.warnings.append(
                    f"layout table {table.name!r} could not be loaded: {first_line}"
                )
    shutil.rmtree(work, ignore_errors=True)
    return report


@dataclass
class _LayoutTable:
    """Rows a layout made for one region of one family, stacked across the family's sheets."""

    name: str
    columns: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    rows: list[list[str | None]] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)
    coverage: float | None = None

    def add(self, source: str, header: list[str], tidy: list[TidyRow]) -> None:
        positions = []
        seen: dict[str, int] = {}
        for name in header:
            # A name with no letters or digits keeps its own text; names that normalize alike in
            # one sheet are numbered, so two columns never share one key.
            key = _normal_name(name) or name.strip() or "column"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = f"{key}_{seen[key]}"
            if key not in self.keys:
                self.keys.append(key)
                self.columns.append(name)
            positions.append(self.keys.index(key))
        for row in tidy:
            cells: list[str | None] = [None] * len(self.keys)
            for position, value in zip(positions, row.cells, strict=True):
                cells[position] = value
            self.rows.append([source, str(row.ordinal), row.cell_range, *cells])
        self.sources[source] = len(tidy)


def _normal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", plain(name)).strip("_")


def _apply_layouts(
    report: IngestReport,
    layout: LayoutSpec,
    files: dict[str, tuple[Path, str]],
    limits: IngestLimits,
) -> list[_LayoutTable]:
    """Apply each family's entries to its sheets. A sheet the layout cannot read (no rows) stays
    on the fast path, with a warning."""
    made: list[_LayoutTable] = []
    known = {f.family for f in report.layout}
    for family in dict.fromkeys(e.family for e in layout.families if e.family not in known):
        report.warnings.append(
            f"layout for family {family} matches no family in these files: not applied"
        )
    for finding in report.layout:
        entries = [e for e in layout.families if e.family == finding.family]
        if not entries:
            continue
        tables = [
            _LayoutTable(name=slug(Path(finding.sources[0].split("#")[0]).stem)) for _ in entries
        ]
        for k, entry in enumerate(entries[1:], start=2):
            tables[k - 1].name += f"_{entry.region}"
        filled = covered = 0
        for source, grid in _full_grids(finding.sources, files, limits):
            applied, coverage = apply_sheet(grid, entries)
            problems = [p for a in applied for p in a.problems]
            if not any(a.rows for a in applied):
                reason = "; ".join(problems) or "no rows"
                report.warnings.append(
                    f"layout for {source} made no rows ({reason}): fast path kept"
                )
                finding.problems.append(f"{source}: {reason}")
                continue
            finding.problems += [f"{source}: {p}" for p in problems]
            for table, result in zip(tables, applied, strict=True):
                table.add(source, result.header, result.rows)
            filled += coverage.filled
            covered += round(coverage.share * coverage.filled)
            room = 20 - len(finding.left)
            finding.left += [f"{source} {address}" for address in coverage.left[:room]]
        if filled:
            finding.coverage = covered / filled
        for table in tables:
            table.coverage = finding.coverage
            if table.rows:
                made.append(table)
    return made


def _full_grids(
    sources: list[str], files: dict[str, tuple[Path, str]], limits: IngestLimits
) -> Iterator[tuple[str, Grid]]:
    """Each wanted sheet in full (up to the row limit), reading every file once."""
    wanted: dict[str, set[str]] = {}
    for source in sources:
        wanted.setdefault(source.split("#", 1)[0], set()).add(source)
    for file_name, chosen in wanted.items():
        if file_name not in files:
            continue
        path, kind = files[file_name]
        if kind == "csv":
            sheets: Iterator[Sheet] = iter([read_csv(path, limits)[0]])
        elif kind == "xlsx":
            sheets = read_xlsx(path)
        else:
            continue
        for sheet in sheets:
            if sheet.source not in chosen:
                continue
            rows: list[Row] = []
            blank_run = 0
            for row in sheet.rows:
                if len(rows) >= limits.max_rows_per_table:
                    break
                blank_run = 0 if any(c not in (None, "") for c in row) else blank_run + 1
                if blank_run > _BLANK_ROW_LIMIT:
                    break
                rows.append(
                    [c[: limits.max_cell_chars] if c else c for c in row[: limits.max_columns]]
                )
            yield sheet.source, Grid(sheet.source, rows, sheet.first_row)


def _load_layout_table(
    connection: duckdb.DuckDBPyConnection, table: _LayoutTable, work: Path
) -> TableReport:
    columns = _column_names(list[str | None](table.columns)) if table.columns else []
    columns += [
        _unique(f"column_{n}", columns) for n in range(len(columns) + 1, len(table.keys) + 1)
    ]
    merged = work / f"layout_{table.name}.csv"
    with merged.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, quoting=csv.QUOTE_NOTNULL, lineterminator=_LINE_END)
        for row in table.rows:
            writer.writerow(row + [None] * (len(columns) + 3 - len(row)))
    report = _create_table(connection, table.name, columns, merged, meta=LAYOUT_META)
    report.sources = [
        SourceReport(source=source, header_row=0, skipped_rows=0, rows=rows)
        for source, rows in table.sources.items()
    ]
    report.row_count = len(table.rows)
    report.coverage = table.coverage
    return report


def _list_files(input_dir: Path, report: IngestReport) -> list[Path]:
    """Regular files only. Symlinks are skipped, and unreadable directories become warnings."""

    def unreadable(error: OSError) -> None:
        where = Path(error.filename).relative_to(input_dir) if error.filename else "?"
        report.warnings.append(f"could not read {where}: {error.strerror}")

    files: list[Path] = []
    for root, _, names in os.walk(input_dir, onerror=unreadable):
        for name in names:
            path = Path(root) / name
            if path.is_file() and not path.is_symlink():
                files.append(path)
    return sorted(files)


def _kind(path: Path) -> str | None:
    name = path.name.lower()
    if ":zone.identifier" in name or name.startswith(("~$", ".")) or name in JUNK_NAMES:
        return "junk"
    suffix = path.suffix.lower()
    if suffix in TABLE_FILES:
        return TABLE_FILES[suffix]
    if suffix in SQLITE_FILES:
        return "sqlite"
    if suffix in DOCUMENT_FILES:
        return "document"
    return None


def _safety_problem(path: Path, kind: str, limits: IngestLimits) -> str | None:
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        return f"file size {size} is over the limit of {limits.max_file_bytes} bytes"
    if kind != "xlsx":
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except zipfile.BadZipFile:
        return None  # The reader reports it as an unreadable file.
    total = sum(member.file_size for member in members)
    if total > limits.max_uncompressed_bytes:
        return f"expands to {total} bytes, over the limit"
    for member in members:
        ratio = member.file_size / max(member.compress_size, 1)
        if member.file_size > 1_000_000 and ratio > limits.max_compression_ratio:
            return f"compression ratio {ratio:.0f} of {member.filename} looks like a zip bomb"
    return None


def _read_file(
    path: Path, kind: str, file_report: FileReport, limits: IngestLimits, prefix: Path
) -> Iterator[tuple[_SheetResult | None, SheetLayout | None]]:
    if kind == "csv":
        sheet, file_report.encoding, file_report.delimiter = read_csv(path, limits)
        sheets: Iterator[Sheet] = iter([sheet])
    elif kind == "xlsx":
        sheets = read_xlsx(path)
    else:
        sheets = read_sqlite(path)
    for number, sheet in enumerate(sheets):
        yield _write_sheet(sheet, limits, prefix.with_name(f"{prefix.name}_{number}.csv"))


def _write_sheet(
    sheet: Sheet, limits: IngestLimits, target: Path
) -> tuple[_SheetResult | None, SheetLayout | None]:
    # Database tables are tidy by construction: only sheets and CSV files get a grid.
    captured: list[Row] = []
    keep = 0 if sheet.first_row == 0 else limits.layout_grid_rows
    rows = _capture(iter(sheet.rows), captured, keep, limits)
    lookahead: list[Row] = []
    for row in rows:
        lookahead.append(row)
        if len(lookahead) >= limits.header_search_rows:
            break
    header_index = 0 if sheet.first_row == 0 else _find_header(lookahead)
    if header_index is None:
        for _ in zip(range(keep), rows, strict=False):
            pass
        return None, _layout(sheet, captured, keep, fast_header_row=0)
    header = _column_names(lookahead[header_index][: limits.max_columns])
    width, count, truncated, long_cells, blank_run = len(header), 0, False, 0, 0
    with target.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, quoting=csv.QUOTE_NOTNULL, lineterminator=_LINE_END)
        body = _chain(lookahead[header_index + 1 :], rows)
        for offset, row in enumerate(body, start=header_index + 1):
            if not any(cell not in (None, "") for cell in row):
                blank_run += 1
                if blank_run > _BLANK_ROW_LIMIT:
                    break  # Formatted but empty sheets can report a million blank rows.
                continue
            blank_run = 0
            if count >= limits.max_rows_per_table:
                truncated = True
                break
            cells = _trim(row[: limits.max_columns])
            long_cells += sum(
                1 for cell in cells if cell is not None and len(cell) > limits.max_cell_chars
            )
            cells = [c[: limits.max_cell_chars] if c is not None else None for c in cells]
            width = max(width, len(cells))
            writer.writerow([sheet.source, str(sheet.first_row + offset), *cells])
            count += 1
    result = _SheetResult(
        source=sheet.source,
        path=target,
        header=header,
        width=width,
        rows=count,
        header_row=sheet.first_row + header_index,
        skipped_rows=header_index,
        truncated=truncated,
        long_cells=long_cells,
    )
    return result, _layout(sheet, captured, keep, result.header_row)


def _capture(
    rows: Iterator[Row], captured: list[Row], keep: int, limits: IngestLimits
) -> Iterator[Row]:
    """Pass rows through, keeping a bounded copy of the first `keep` of them."""
    for row in rows:
        if len(captured) < keep:
            captured.append(
                [c[: limits.max_cell_chars] if c else c for c in row[: limits.max_columns]]
            )
        yield row


def _layout(
    sheet: Sheet, captured: list[Row], keep: int, fast_header_row: int
) -> SheetLayout | None:
    if not keep or not captured:
        return None
    grid = Grid(sheet.source, captured, sheet.first_row)
    return describe_sheet(grid, fast_header_row=fast_header_row)


def _chain(first: list[Row], rest: Iterator[Row]) -> Iterator[Row]:
    yield from first
    yield from rest


def _trim(row: Row) -> Row:
    end = len(row)
    while end and row[end - 1] in (None, ""):
        end -= 1
    return list(row[:end])


def _filled(row: Row) -> list[str]:
    return [cell for cell in row if cell is not None and cell.strip()]


def _find_header(rows: list[Row]) -> int | None:
    """The first row that is wide enough, compared with the rows below it, and mostly text."""
    for index, row in enumerate(rows):
        cells = _filled(row)
        if not cells:
            continue
        below = [len(_filled(r)) for r in rows[index + 1 : index + 21]]
        width = max([n for n in below if n] or [len(cells)])
        text_share = sum(1 for c in cells if classify(c) == "text") / len(cells)
        if len(cells) >= max(1, math.ceil(width / 2)) and text_share >= _HEADER_TEXT_SHARE:
            return index
    return None


def _column_names(cells: Row) -> list[str]:
    names: list[str] = []
    for position, cell in enumerate(_trim(cells), start=1):
        base = (cell or "").strip() or f"column_{position}"
        names.append(_unique(base, names))
    return names


def _unique(base: str, taken: list[str] | set[str]) -> str:
    name, suffix = base, 2
    while name in taken or name in LAYOUT_META:
        name, suffix = f"{base}_{suffix}", suffix + 1
    return name


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_") or "table"


def _register(
    sheets: list[_SheetResult],
    path: Path,
    kind: str,
    groups: dict[tuple[str, ...], _Group],
    names: set[str],
) -> None:
    file_has_group = False
    for sheet in sheets:
        key = tuple(sheet.header)
        if key not in groups:
            if kind == "sqlite":
                base = slug(sheet.source.split("#", 1)[1])
            elif file_has_group:
                base = f"{slug(path.stem)}_{slug(sheet.source.split('#', 1)[1])}"
            else:
                base = slug(path.stem)
            name = _unique(base, names)
            names.add(name)
            groups[key] = _Group(name, sheet.header)
        groups[key].sheets.append(sheet)
        file_has_group = True


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _load_group(connection: duckdb.DuckDBPyConnection, group: _Group, work: Path) -> TableReport:
    width = max(sheet.width for sheet in group.sheets)
    columns = list(group.header)
    for position in range(len(columns) + 1, width + 1):
        columns.append(_unique(f"column_{position}", columns))
    merged = work / f"{group.name}.csv"
    with merged.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, quoting=csv.QUOTE_NOTNULL, lineterminator=_LINE_END)
        for sheet in group.sheets:
            with sheet.path.open(newline="", encoding="utf-8") as source:
                for record in csv.reader(source):
                    cells: list[str | None] = [c if c != "" else None for c in record]
                    writer.writerow(cells + [None] * (width + 2 - len(cells)))
    report = _create_table(connection, group.name, columns, merged, meta=FAST_META)
    long_cells = sum(sheet.long_cells for sheet in group.sheets)
    if long_cells and report.columns:
        report.columns[0].profile.truncated_cells = long_cells
    report.sources = [
        SourceReport(
            source=s.source, header_row=s.header_row, skipped_rows=s.skipped_rows, rows=s.rows
        )
        for s in group.sheets
    ]
    report.row_count = sum(sheet.rows for sheet in group.sheets)
    report.truncated = any(sheet.truncated for sheet in group.sheets)
    return report


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    name: str,
    columns: list[str],
    merged: Path,
    meta: tuple[str, ...],
) -> TableReport:
    """Load a work CSV whose rows are the meta columns, then the values, all as text."""
    positional = {f"c{i}": "VARCHAR" for i in range(len(columns) + len(meta))}
    select = ", ".join(
        [f"c{i + len(meta)} AS {_quote(column)}" for i, column in enumerate(columns)]
        + [f"c{i} AS {m}" for i, m in enumerate(meta)]
    )
    reader = (
        f"read_csv(?, header=false, auto_detect=false, delim=',', quote='\"', escape='\"', "
        f"new_line='\\n', allow_quoted_nulls=false, columns={positional!r})"
    )
    connection.execute(
        f"CREATE TABLE {_quote(name)} AS SELECT {select} FROM {reader}",  # noqa: S608
        [str(merged)],
    )
    profiles = {column: _profile(connection, name, column) for column in columns}
    reports = [ColumnReport(name=column, profile=profiles[column]) for column in columns]
    reports += [ColumnReport(name=m, profile=ColumnProfile()) for m in meta]
    sensitive = {column for column in columns if profiles[column].sensitive}
    return TableReport(
        name=name,
        sources=[],
        row_count=0,
        columns=reports,
        rare_shapes=_rare_shapes(connection, name, columns, sensitive),
    )


def _rare_shapes(
    connection: duckdb.DuckDBPyConnection, table: str, columns: list[str], sensitive: set[str]
) -> list[RowShape]:
    """Rare sets of filled columns, sparser than the common one, with a few example rows."""
    if not columns:
        return []
    filled = [f"({_quote(c)} IS NOT NULL AND trim({_quote(c)}) <> '')" for c in columns]
    shape = f"list_value({', '.join(filled)})"
    counts = connection.execute(
        f"SELECT {shape} AS shape, count(*) AS n FROM {_quote(table)} "  # noqa: S608
        "GROUP BY shape ORDER BY n DESC, min(rowid)"
    ).fetchall()
    if len(counts) < 2:
        return []
    total, common = sum(n for _, n in counts), sum(counts[0][0])
    rare = [
        ([c for c, flag in zip(columns, flags, strict=True) if flag], flags, n)
        for flags, n in counts[1:]
        if n / total < RARE_SHAPE_SHARE and sum(flags) < common
    ]
    every = [names for names, _, _ in rare]
    shapes: list[RowShape] = []
    for names, flags, n in rare[:MAX_RARE_SHAPES]:
        sums = connection.execute(sums_above_sql(_quote(table), columns, names, every)).fetchone()
        shapes.append(
            RowShape(
                columns=names,
                rows=n,
                examples=_shape_examples(connection, table, columns, filled, flags, sensitive),
                sums_above=int(sums[0]) if sums else 0,
            )
        )
    return shapes


def _shape_examples(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[str],
    filled: list[str],
    flags: list[bool],
    sensitive: set[str],
) -> list[dict[str, str | None]]:
    match = " AND ".join(f if flag else f"NOT {f}" for f, flag in zip(filled, flags, strict=True))
    names = [c for c, flag in zip(columns, flags, strict=True) if flag]
    selected = ", ".join([ROW_COLUMN, *(_quote(c) for c in names)])
    rows = connection.execute(
        f"SELECT {selected} FROM {_quote(table)} WHERE {match} "  # noqa: S608
        f"ORDER BY rowid LIMIT {SHAPE_EXAMPLES}"
    ).fetchall()
    examples: list[dict[str, str | None]] = []
    for row_number, *values in rows:
        example: dict[str, str | None] = {ROW_COLUMN: row_number}
        for name, value in zip(names, values, strict=True):
            example[name] = SENSITIVE_MARK if name in sensitive else str(value)[:SAMPLE_CHARS]
        examples.append(example)
    return examples


def _profile(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> ColumnProfile:
    stats = ColumnStats(column)
    query = (
        f"SELECT {_quote(column)}, count(*) FROM {_quote(table)} "  # noqa: S608
        f"WHERE {_quote(column)} IS NOT NULL GROUP BY 1"
    )
    for value, count in connection.execute(query).fetchall():
        stats.add(value, count)
    return stats.profile()
