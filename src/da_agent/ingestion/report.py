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
    # Rows whose value in a number column equals the sum of the record rows above them, back to
    # the previous row of this shape in the same sheet: the mark of a subtotal.
    sums_above: int = 0


class TableReport(BaseModel):
    name: str
    sources: list[SourceReport]
    row_count: int
    truncated: bool = False
    columns: list[ColumnReport]
    rare_shapes: list[RowShape] = Field(default_factory=list)
    # Set when a layout made the table: the share of its sheets' filled cells it accounted for.
    coverage: float | None = None


class FamilyFinding(BaseModel):
    """Sheets that share one layout, found by code, and why they need a layout spec."""

    family: str
    sources: list[str]
    flags: list[str]
    regions: int
    # Each region of the first sheet as a range, such as "A1:F74", in the order layouts number them.
    region_bounds: list[str] = Field(default_factory=list)
    # Header cell texts of the first sheet, every layer, for checking names a layout uses.
    columns: list[str] = Field(default_factory=list)
    context_candidates: list[str] = Field(default_factory=list)
    # Title and side-block cells whose text differs between the family's sheets.
    varying_cells: list[str] = Field(default_factory=list)
    # What code noticed in the first sheet, in words, such as rows that come in pairs.
    hints: list[str] = Field(default_factory=list)
    preview: str = ""
    # Set when a layout was applied to the family.
    coverage: float | None = None
    left: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class IngestReport(BaseModel):
    files: list[FileReport] = Field(default_factory=list)
    tables: list[TableReport] = Field(default_factory=list)
    documents: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    layout: list[FamilyFinding] = Field(default_factory=list)
