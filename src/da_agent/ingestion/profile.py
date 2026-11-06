"""Column profiles: counts, samples, and value patterns, computed while rows stream past."""

import re
import unicodedata
from collections import Counter

from da_agent.ingestion.report import ColumnProfile

MAX_TRACKED_VALUES = 10_000
SAMPLES = 5
SAMPLE_CHARS = 60

# Ordered: the first matching pattern names the value.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("integer", re.compile(r"^-?\d+$")),
    ("decimal", re.compile(r"^-?\d+\.\d+$")),
    ("number_grouped", re.compile(r"^-?\d{1,3}([,.]\d{3})+$")),
    ("money_suffix", re.compile(r"^-?\d[\d.,]*\s*(k|đ|d|vnd|vnđ|tr|triệu|nghìn|ngàn)$", re.I)),
    ("datetime_iso", re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")),
    ("date_iso", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("date_dmy", re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")),
    ("date_text_vi", re.compile(r"^\d{1,2}\s+(thg|tháng)\s*\d{1,2}(,?\s+\d{4})?$", re.I)),
    ("month", re.compile(r"^(\d{1,2}[/-]\d{4}|\d{4}-\d{2})$")),
    ("boolean", re.compile(r"^(true|false)$")),
    ("phone", re.compile(r"^(\+?84|0)\d{9,10}$")),
    # 12-digit citizen ID. Old 9-digit IDs look like ordinary numbers such as 999999999.
    ("id_number", re.compile(r"^\d{12}$")),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("quantity_unit", re.compile(r"^-?\d+([.,]\d+)?\s*[^\W\d_][\w\s]{0,15}$")),
]
# Checked before the generic patterns, so a phone number is not reported as an integer.
_PRIORITY = ("phone", "id_number", "email")
SENSITIVE_PATTERNS = set(_PRIORITY)
SENSITIVE_NAME_WORDS = (
    "cccd", "cmnd", "email", "e-mail", "phone", "sdt", "so dien thoai", "dien thoai",
    "ngay sinh", "birth", "dia chi", "address", "passport", "ho chieu", "mst", "tax",
)  # fmt: skip


def _plain(text: str) -> str:
    text = text.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def classify(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "blank"
    for name, pattern in PATTERNS:
        if name in _PRIORITY and pattern.match(stripped):
            return name
    for name, pattern in PATTERNS:
        if name not in _PRIORITY and pattern.match(stripped):
            return name
    return "text"


class ColumnStats:
    def __init__(self, name: str) -> None:
        self.name = name
        self.non_null = 0
        self.values: Counter[str] = Counter()
        self.capped = False
        self.patterns: Counter[str] = Counter()
        self.truncated_cells = 0

    def add(self, value: str | None, count: int = 1) -> None:
        if value is None:
            return
        self.non_null += count
        self.patterns[classify(value)] += count
        if value in self.values or len(self.values) < MAX_TRACKED_VALUES:
            self.values[value] += count
        else:
            self.capped = True

    def profile(self) -> ColumnProfile:
        sensitive = self._sensitive()
        samples = (
            [] if sensitive else [v[:SAMPLE_CHARS] for v, _ in self.values.most_common(SAMPLES)]
        )
        return ColumnProfile(
            non_null=self.non_null,
            distinct=len(self.values),
            distinct_capped=self.capped,
            samples=samples,
            patterns=dict(self.patterns.most_common()),
            sensitive=sensitive,
            truncated_cells=self.truncated_cells,
        )

    def _sensitive(self) -> bool:
        plain_name = _plain(self.name).replace("_", " ")
        if any(word in plain_name for word in SENSITIVE_NAME_WORDS):
            return True
        if not self.non_null:
            return False
        flagged = sum(self.patterns[p] for p in SENSITIVE_PATTERNS)
        return flagged / self.non_null >= 0.5
