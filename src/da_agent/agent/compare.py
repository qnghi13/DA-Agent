"""Whether a rerun gave the same answer as a golden case (ADR 0043).

Product code, not the DataMate scorer: answers match when they have the same kind and the same
value. Numbers may differ by a relative 0.01%, text compares trimmed and case-folded, lists ignore
order, and tables match by column name with rows in any order. Inside lists and tables only real
JSON numbers get the tolerance, and whole numbers must be equal: strings such as ids, codes, and
phone numbers ("0912345678", "007") compare as text. A malformed value never raises; it is
"changed", so a rerun report always completes.
"""

import math
from typing import Any, Literal

Outcome = Literal["same", "changed", "no_answer"]
RELATIVE_TOLERANCE = 1e-4


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _same_cell(expected: Any, actual: Any) -> bool:
    """One list item or table cell. Whole numbers match exactly, other numbers within the
    tolerance, and everything else, numeric-looking strings included, as trimmed text."""
    if _is_number(expected) and _is_number(actual):
        if isinstance(expected, int) and isinstance(actual, int):
            return expected == actual
        return math.isclose(expected, actual, rel_tol=RELATIVE_TOLERANCE, abs_tol=1e-9)
    if expected is None or actual is None or isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual or (expected == actual and type(expected) is type(actual))
    return str(expected).strip().casefold() == str(actual).strip().casefold()


def _same_bag(expected: list[Any], actual: list[Any], same: Any) -> bool:
    """Whether every expected item pairs with its own actual item, in any order."""
    if len(expected) != len(actual):
        return False
    left = list(actual)
    for item in expected:
        match = next((i for i, other in enumerate(left) if same(item, other)), None)
        if match is None:
            return False
        left.pop(match)
    return True


def _same_row(expected: list[Any], actual: list[Any]) -> bool:
    return len(expected) == len(actual) and all(map(_same_cell, expected, actual))


def _same_scalar(kind: str, expected: Any, actual: Any) -> bool:
    if kind in ("number", "percent"):
        left, right = _number(expected), _number(actual)
        if left is None or right is None:
            return False
        return math.isclose(left, right, rel_tol=RELATIVE_TOLERANCE, abs_tol=1e-9)
    if kind == "boolean":
        return isinstance(actual, bool) and expected is actual
    return str(expected).strip().casefold() == str(actual).strip().casefold()


def _same_table(expected: Any, actual: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    columns, other_columns = expected.get("columns"), actual.get("columns")
    rows, other_rows = expected.get("rows"), actual.get("rows")
    if not (
        isinstance(columns, list)
        and isinstance(other_columns, list)
        and isinstance(rows, list)
        and isinstance(other_rows, list)
    ):
        return False
    names = [str(name).strip().casefold() for name in columns]
    other_names = [str(name).strip().casefold() for name in other_columns]
    if sorted(names) != sorted(other_names):
        return False
    if len(set(names)) == len(names):
        order = [other_names.index(name) for name in names]
    elif names == other_names:
        order = list(range(len(names)))  # Repeated names: only the same order can match.
    else:
        return False
    try:
        reordered = [[row[i] for i in order] for row in other_rows]
    except (IndexError, TypeError):
        return False
    if not all(isinstance(row, list) for row in rows):
        return False
    return _same_bag(rows, reordered, _same_row)


def compare_answers(expected: dict[str, Any], actual: dict[str, Any] | None) -> Outcome:
    if actual is None:
        return "no_answer"
    kind = expected.get("kind")
    if actual.get("kind") != kind:
        return "changed"
    value, other = expected.get("value"), actual.get("value")
    if kind == "unanswerable":
        return "same"
    if kind == "list":
        if not isinstance(value, list) or not isinstance(other, list):
            return "changed"
        same = _same_bag(value, other, _same_cell)
    elif kind == "table":
        same = _same_table(value, other)
    else:
        same = _same_scalar(str(kind), value, other)
    return "same" if same else "changed"
