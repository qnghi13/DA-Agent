"""Scorer for DataMate submissions, following section 9 of the rulebook.

Scored items and reports carry only outcomes and reason codes, never expected answers, so they are
safe to pass to analysis tools or show in logs.
"""

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

# Ignores floating-point noise only. A relative tolerance would forgive whole dong on large amounts.
NUMBER_TOLERANCE = 1e-6

DATE_PATTERN = re.compile(r"([0-9]{4})-([0-9]{2})(?:-([0-9]{2}))?")

FORMAT = "format"
VALUE = "value"


class Outcome(Enum):
    CORRECT = "correct"
    WRONG = "wrong"
    ABSTAIN = "abstain"


POINTS = {Outcome.CORRECT: 1, Outcome.WRONG: -1, Outcome.ABSTAIN: 0}


@dataclass(frozen=True)
class ItemScore:
    id: str
    type: str
    outcome: Outcome
    reason: str | None = None

    @property
    def points(self) -> int:
        return POINTS[self.outcome]


@dataclass(frozen=True)
class Report:
    items: list[ItemScore]
    unknown_ids: list[str]

    def _count(self, outcome: Outcome) -> int:
        return sum(item.outcome is outcome for item in self.items)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def correct(self) -> int:
        return self._count(Outcome.CORRECT)

    @property
    def wrong(self) -> int:
        return self._count(Outcome.WRONG)

    @property
    def abstained(self) -> int:
        return self._count(Outcome.ABSTAIN)

    @property
    def score(self) -> int:
        return sum(item.points for item in self.items)

    @property
    def by_type(self) -> dict[str, dict[str, int]]:
        breakdown: dict[str, dict[str, int]] = {}
        for item in self.items:
            row = breakdown.setdefault(
                item.type, {"correct": 0, "wrong": 0, "abstained": 0, "score": 0}
            )
            key = "abstained" if item.outcome is Outcome.ABSTAIN else item.outcome.value
            row[key] += 1
            row["score"] += item.points
        return breakdown

    def summary(self) -> dict[str, Any]:
        """Return totals for logs and reports, with no per-question data."""
        return {
            "total": self.total,
            "score": self.score,
            "correct": self.correct,
            "wrong": self.wrong,
            "abstained": self.abstained,
            "wrong_reasons": dict(Counter(item.reason for item in self.items if item.reason)),
            "by_type": self.by_type,
            "unknown_ids": self.unknown_ids,
        }


def normalize_text(text: str) -> str:
    """Drop case, whitespace, and punctuation. Vietnamese diacritics stay significant."""
    composed = unicodedata.normalize("NFC", text).casefold()
    return "".join(
        char
        for char in composed
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "Z"}
    )


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _check_number(expected: float, given: Any) -> str | None:
    if not _is_number(given):
        return FORMAT
    try:
        return None if abs(expected - given) <= NUMBER_TOLERANCE else VALUE
    except OverflowError:
        return VALUE


def _is_valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = DATE_PATTERN.fullmatch(value)
    if match is None:
        return False
    year, month, day = match.groups()
    try:
        date(int(year), int(month), int(day or 1))
    except ValueError:
        return False
    return True


def _check_date(expected: str, given: Any) -> str | None:
    if not _is_valid_date(given):
        return FORMAT
    return None if given == expected else VALUE


def _check_boolean(expected: bool, given: Any) -> str | None:
    if not isinstance(given, bool):
        return FORMAT
    return None if given is expected else VALUE


def _check_string(expected: str, given: Any, aliases: Iterable[str] = ()) -> str | None:
    if not isinstance(given, str):
        return FORMAT
    accepted = {normalize_text(expected), *(normalize_text(alias) for alias in aliases)}
    return None if normalize_text(given) in accepted else VALUE


def _check_scalar(expected: Any, given: Any, aliases: Iterable[str] = ()) -> str | None:
    if isinstance(expected, bool):
        return _check_boolean(expected, given)
    if isinstance(expected, int | float):
        return _check_number(expected, given)
    return _check_string(expected, given, aliases)


def _check_list(expected: list, given: Any, aliases: list) -> str | None:
    if not isinstance(given, list):
        return FORMAT
    results = [
        _check_scalar(item, other, aliases[index] if aliases else ())
        for index, (item, other) in enumerate(zip(expected, given, strict=False))
    ]
    if FORMAT in results:
        return FORMAT
    if len(expected) != len(given) or VALUE in results:
        return VALUE
    return None


def _column_order(expected: list[str], given: list[str]) -> list[int]:
    """Map each expected column to a submitted column: by name, or by position if names differ."""
    names = [normalize_text(name) for name in given]
    wanted = [normalize_text(name) for name in expected]
    if sorted(names) == sorted(wanted) and len(set(names)) == len(names):
        return [names.index(name) for name in wanted]
    return list(range(len(expected)))


def _is_table(value: Any, width: int) -> bool:
    if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
        return False
    columns, rows = value["columns"], value["rows"]
    return (
        isinstance(columns, list)
        and len(columns) == width
        and all(isinstance(column, str) for column in columns)
        and isinstance(rows, list)
        and all(isinstance(row, list) and len(row) == width for row in rows)
    )


def _check_table(expected: dict, given: Any, aliases: list) -> str | None:
    columns, rows = expected["columns"], expected["rows"]
    if not _is_table(given, len(columns)):
        return FORMAT

    order = _column_order(columns, given["columns"])
    candidates = [[row[index] for index in order] for row in given["rows"]]

    # Compare every cell with the same column of the first row, to find format errors in any row.
    if rows and any(
        _check_scalar(rows[0][index], cell) == FORMAT
        for row in candidates
        for index, cell in enumerate(row)
    ):
        return FORMAT
    if len(candidates) != len(rows):
        return VALUE

    for row_index, row in enumerate(rows):
        row_aliases = aliases[row_index] if aliases else ()
        for candidate in candidates:
            if _check_scalar(row[0], candidate[0], row_aliases) is None and all(
                _check_scalar(cell, other) is None
                for cell, other in zip(row[1:], candidate[1:], strict=True)
            ):
                candidates.remove(candidate)
                break
        else:
            return VALUE
    return None


def _check_unanswerable(submission: Mapping[str, Any]) -> str | None:
    reason = submission.get("reason")
    return None if isinstance(reason, str) and reason.strip() else FORMAT


def _check(truth: Mapping[str, Any], submission: Mapping[str, Any]) -> str | None:
    kind = truth["type"]
    if kind == "unanswerable":
        return _check_unanswerable(submission)

    expected, aliases = truth["answer"], truth.get("alias") or []
    given = submission.get("answer")
    if kind in {"number", "percent"}:
        return _check_number(expected, given)
    if kind == "date":
        return _check_date(expected, given)
    if kind == "boolean":
        return _check_boolean(expected, given)
    if kind == "string":
        return _check_string(expected, given, aliases)
    if kind == "list":
        return _check_list(expected, given, aliases)
    if kind == "table":
        return _check_table(expected, given, aliases)
    raise ValueError(f"unknown answer type in truth for {truth['id']}: {kind!r}")


def score_item(truth: Mapping[str, Any], submission: Mapping[str, Any] | None) -> ItemScore:
    """Score one question. A missing submission counts as an abstention."""
    id_, kind = truth["id"], truth["type"]
    if submission is None or submission.get("type") == "abstain":
        return ItemScore(id_, kind, Outcome.ABSTAIN)
    if submission.get("type") != kind:
        return ItemScore(id_, kind, Outcome.WRONG, "type")
    reason = _check(truth, submission)
    return ItemScore(id_, kind, Outcome.CORRECT if reason is None else Outcome.WRONG, reason)


def _index_by_id(rows: Iterable[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        id_ = row.get("id")
        if not isinstance(id_, str) or not id_:
            raise ValueError(f"{label} row has no id")
        if id_ in indexed:
            raise ValueError(f"duplicate {label} id: {id_}")
        indexed[id_] = row
    return indexed


def score_submission(
    truths: Iterable[Mapping[str, Any]], submissions: Iterable[Mapping[str, Any]]
) -> Report:
    """Score a submission against the given questions. Other submitted ids earn no points."""
    truth_by_id = _index_by_id(truths, "truth")
    submission_by_id = _index_by_id(submissions, "submission")
    items = [score_item(truth, submission_by_id.get(id_)) for id_, truth in truth_by_id.items()]
    unknown_ids = sorted(set(submission_by_id) - set(truth_by_id))
    return Report(items, unknown_ids)


def load_answer_types(path: Path) -> dict[str, str]:
    """Return only each question's answer type, for tools that must not see the answers."""
    return {row["id"]: row["type"] for row in load_jsonl(path)}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSON objects, one per line. Errors name the file and line."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: invalid JSON: {error.msg}") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{number}: expected a JSON object")
            rows.append(row)
    return rows
