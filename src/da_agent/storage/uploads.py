"""Names for uploaded files on disk.

An uploaded name is untrusted: it can hold directories (`../../x`), be hidden (`.x`), or name a
type the ingestion cannot read. The stored name keeps only the last path part, in plain ASCII
letters, digits, `_` and `-`, with an allowed extension. It stays readable because ingestion
names tables after files. The original name is kept as metadata only.
"""

import re

from da_agent.agent.rules import normalize
from da_agent.ingestion.raw import DOCUMENT_FILES, SQLITE_FILES, TABLE_FILES

ALLOWED_SUFFIXES = frozenset({*TABLE_FILES, *SQLITE_FILES, *DOCUMENT_FILES})
MAX_STEM_CHARS = 100


class UploadRejectedError(ValueError):
    """The upload cannot be accepted. The message is safe to show to the uploader."""


def storage_name(original: str, taken: set[str]) -> str:
    """A safe name for an uploaded file that does not collide with names in taken."""
    last = re.split(r"[/\\]", original)[-1]
    stem, dot, suffix = last.rpartition(".")
    suffix = f".{suffix.lower()}" if dot else ""
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise UploadRejectedError(f"file type of {last!r} is not supported; use one of {allowed}")
    plain = re.sub(r"[^A-Za-z0-9_-]+", "_", normalize_case_kept(stem)).strip("_-")
    base = (plain or "file")[:MAX_STEM_CHARS]
    name, number = f"{base}{suffix}", 2
    while name in taken:
        name, number = f"{base}_{number}{suffix}", number + 1
    return name


def normalize_case_kept(text: str) -> str:
    """Text without diacritics, keeping letter case: 'Đơn' becomes 'Don'."""
    return "".join(normalize(char).upper() if char.isupper() else normalize(char) for char in text)
