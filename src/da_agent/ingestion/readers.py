"""Readers turn one file into sheets of text rows. Every value becomes text or None, losslessly."""

import codecs
import csv
import datetime as dt
import io
import sqlite3
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from da_agent.ingestion.limits import IngestLimits

Row = list[str | None]
CSV_DELIMITERS = ",;\t|"
FALLBACK_ENCODINGS = ("cp1258", "latin-1")


@dataclass
class Sheet:
    """Rows of one CSV file, worksheet, or SQLite table. `first_row` is the number of rows[0]."""

    source: str
    rows: Iterator[Row]
    first_row: int = 1
    has_header: bool = True


class ReadError(Exception):
    """The file could not be read. The message is safe to show to the user."""


def to_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return unicodedata.normalize("NFC", str(value))


def detect_encoding(head: bytes, data: bytes) -> str:
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    for encoding in FALLBACK_ENCODINGS:
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ReadError("unknown text encoding")


def read_csv(path: Path, limits: IngestLimits) -> tuple[Sheet, str, str]:
    data = path.read_bytes()
    encoding = detect_encoding(data[:4], data)
    # NFC, so the same letter with a diacritic compares equal however the file encoded it.
    text = unicodedata.normalize("NFC", data.decode(encoding))
    try:
        delimiter = csv.Sniffer().sniff(text[:65_536], delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        delimiter = ","
    csv.field_size_limit(max(limits.max_cell_chars * 10, 131_072))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    def rows() -> Iterator[Row]:
        try:
            for record in reader:
                yield [cell if cell != "" else None for cell in record]
        except csv.Error as error:
            raise ReadError(f"malformed CSV: {error}") from error

    # An empty field becomes None. csv.reader cannot tell "" from a missing value in Python 3.12.
    return Sheet(path.name, rows()), encoding, delimiter


def read_xlsx(path: Path) -> Iterator[Sheet]:
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as error:  # openpyxl raises many types for bad files
        raise ReadError(f"not a readable xlsx file: {type(error).__name__}") from error
    try:
        for worksheet in book.worksheets:
            rows = (
                [_xlsx_text(value) for value in row]
                for row in worksheet.iter_rows(values_only=True)
            )
            yield Sheet(f"{path.name}#{worksheet.title}", rows)
    finally:
        book.close()


def _xlsx_text(value: object) -> str | None:
    text = to_text(value)
    return None if text == "" else text


def read_sqlite(path: Path) -> Iterator[Sheet]:
    uri = f"file:{path}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
    except sqlite3.DatabaseError as error:
        raise ReadError("not a readable SQLite database") from error
    try:
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {quoted}")  # noqa: S608 - quoted name
            header: Row = [d[0] for d in cursor.description]
            body = ([to_text(value) for value in row] for row in cursor)
            yield Sheet(f"{path.name}#{name}", _prepend(header, body), first_row=0)
    finally:
        connection.close()


def _prepend(first: Row, rest: Iterator[Row]) -> Iterator[Row]:
    yield first
    yield from rest
