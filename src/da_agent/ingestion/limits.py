"""Limits applied before and during parsing. Operators set them; uploads cannot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestLimits:
    max_files: int = 200
    max_file_bytes: int = 200_000_000
    # xlsx files are zip archives: bound what they expand to, in total and per member.
    max_uncompressed_bytes: int = 1_000_000_000
    max_compression_ratio: int = 200
    max_rows_per_table: int = 2_000_000
    max_columns: int = 300
    max_cell_chars: int = 10_000
    max_document_chars: int = 50_000
    header_search_rows: int = 30
