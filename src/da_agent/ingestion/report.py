"""The raw ingestion report. It is the only thing the Worker reads from the sandbox."""

from typing import Literal

from pydantic import BaseModel, Field

FileStatus = Literal["loaded", "document", "ignored", "unsupported", "rejected", "error"]


class FileReport(BaseModel):
    path: str
    status: FileStatus
    reason: str | None = None
    encoding: str | None = None
    delimiter: str | None = None


class SourceReport(BaseModel):
    """Where rows of a table came from: one CSV file, one sheet, or one SQLite table."""

    source: str
    header_row: int
    skipped_rows: int
    rows: int


class ColumnProfile(BaseModel):
    non_null: int = 0
    distinct: int = 0
    distinct_capped: bool = False
    samples: list[str] = Field(default_factory=list)
    patterns: dict[str, int] = Field(default_factory=dict)
    sensitive: bool = False
    truncated_cells: int = 0


class ColumnReport(BaseModel):
    name: str
    profile: ColumnProfile


class RowShape(BaseModel):
    """A rare set of filled columns, such as subtotal or note rows inside a table."""

    columns: list[str]
    rows: int
    examples: list[dict[str, str | None]] = Field(default_factory=list)


class TableReport(BaseModel):
    name: str
    sources: list[SourceReport]
    row_count: int
    truncated: bool = False
    columns: list[ColumnReport]
    rare_shapes: list[RowShape] = Field(default_factory=list)


class IngestReport(BaseModel):
    files: list[FileReport] = Field(default_factory=list)
    tables: list[TableReport] = Field(default_factory=list)
    documents: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
