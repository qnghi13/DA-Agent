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

from da_agent.ingestion.limits import IngestLimits
from da_agent.ingestion.profile import ColumnStats, classify
from da_agent.ingestion.readers import ReadError, Row, Sheet, read_csv, read_sqlite, read_xlsx
from da_agent.ingestion.report import (
    ColumnProfile,
    ColumnReport,
    FileReport,
    IngestReport,
    SourceReport,
    TableReport,
)

RAW_DATABASE = "raw.duckdb"
SOURCE_COLUMN, ROW_COLUMN = "_source", "_row"
TABLE_FILES = {".csv": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".xlsm": "xlsx"}
SQLITE_FILES = {".sqlite", ".sqlite3", ".db"}
DOCUMENT_FILES = {".md", ".txt"}
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_HEADER_TEXT_SHARE = 0.8
_BLANK_ROW_LIMIT = 1000


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


def ingest(input_dir: Path, out_dir: Path, limits: IngestLimits | None = None) -> IngestReport:
    limits = limits or IngestLimits()
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    report = IngestReport()
    groups: dict[tuple[str, ...], _Group] = {}
    names: set[str] = set()

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
            sheets = list(_read_file(path, kind, file_report, limits, work / f"f{index}"))
        except (ReadError, OSError, ValueError, csv.Error) as error:
            file_report.status, file_report.reason = "error", str(error)[:300]
            continue
        _register(sheets, path, kind, groups, names)

    with duckdb.connect(str(out_dir / RAW_DATABASE)) as connection:
        for group in groups.values():
            report.tables.append(_load_group(connection, group, work))
    shutil.rmtree(work, ignore_errors=True)
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
) -> Iterator[_SheetResult]:
    if kind == "csv":
        sheet, file_report.encoding, file_report.delimiter = read_csv(path, limits)
        sheets: Iterator[Sheet] = iter([sheet])
    elif kind == "xlsx":
        sheets = read_xlsx(path)
    else:
        sheets = read_sqlite(path)
    for number, sheet in enumerate(sheets):
        result = _write_sheet(sheet, limits, prefix.with_name(f"{prefix.name}_{number}.csv"))
        if result is not None:
            yield result


def _write_sheet(sheet: Sheet, limits: IngestLimits, target: Path) -> _SheetResult | None:
    rows = iter(sheet.rows)
    lookahead: list[Row] = []
    for row in rows:
        lookahead.append(row)
        if len(lookahead) >= limits.header_search_rows:
            break
    header_index = 0 if sheet.first_row == 0 else _find_header(lookahead)
    if header_index is None:
        return None
    header = _column_names(lookahead[header_index][: limits.max_columns])
    width, count, truncated, long_cells, blank_run = len(header), 0, False, 0, 0
    with target.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, quoting=csv.QUOTE_NOTNULL)
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
    return _SheetResult(
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
    while name in taken or name in (SOURCE_COLUMN, ROW_COLUMN):
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
        writer = csv.writer(out, quoting=csv.QUOTE_NOTNULL)
        for sheet in group.sheets:
            with sheet.path.open(newline="", encoding="utf-8") as source:
                for record in csv.reader(source):
                    cells: list[str | None] = [c if c != "" else None for c in record]
                    writer.writerow(cells + [None] * (width + 2 - len(cells)))
    positional = {f"c{i}": "VARCHAR" for i in range(width + 2)}
    select = ", ".join(
        [f"c{i + 2} AS {_quote(name)}" for i, name in enumerate(columns)]
        + [f"c0 AS {SOURCE_COLUMN}", f"c1 AS {ROW_COLUMN}"]
    )
    reader = (
        f"read_csv(?, header=false, auto_detect=false, delim=',', quote='\"', escape='\"', "
        f"allow_quoted_nulls=false, columns={positional!r})"
    )
    connection.execute(
        f"CREATE TABLE {_quote(group.name)} AS SELECT {select} FROM {reader}",  # noqa: S608
        [str(merged)],
    )
    profiles = {name: _profile(connection, group.name, name) for name in columns}
    long_cells = sum(sheet.long_cells for sheet in group.sheets)
    reports = [ColumnReport(name=name, profile=profiles[name]) for name in columns]
    reports += [ColumnReport(name=n, profile=ColumnProfile()) for n in (SOURCE_COLUMN, ROW_COLUMN)]
    if long_cells and reports:
        reports[0].profile.truncated_cells = long_cells
    return TableReport(
        name=group.name,
        sources=[
            SourceReport(
                source=s.source, header_row=s.header_row, skipped_rows=s.skipped_rows, rows=s.rows
            )
            for s in group.sheets
        ],
        row_count=sum(sheet.rows for sheet in group.sheets),
        truncated=any(sheet.truncated for sheet in group.sheets),
        columns=reports,
    )


def _profile(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> ColumnProfile:
    stats = ColumnStats(column)
    query = (
        f"SELECT {_quote(column)}, count(*) FROM {_quote(table)} "  # noqa: S608
        f"WHERE {_quote(column)} IS NOT NULL GROUP BY 1"
    )
    for value, count in connection.execute(query).fetchall():
        stats.add(value, count)
    return stats.profile()
