"""Whether a rerun gave the same answer as a golden case (ADR 0043).

Product code, not the DataMate scorer: answers match when they have the same kind and the same
value. Numbers may differ by a relative 0.01%, text compares trimmed and case-folded, lists ignore
order, and tables match by column name with rows in any order. A malformed value never raises; it
is "changed", so a rerun report always completes.
"""

import math
from collections import Counter
from typing import Any, Literal

Outcome = Literal["same", "changed", "no_answer"]
RELATIVE_TOLERANCE = 1e-4
# Numbers inside lists and tables are rounded to this many significant digits before matching.
SIGNIFICANT_DIGITS = 5


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _key(value: Any) -> Any:
    """A form of one cell or list item that compares equal when the values match."""
    number = _number(value)
    if number is not None and math.isfinite(number):
        return ("n", float(f"{number:.{SIGNIFICANT_DIGITS}g}"))
    if isinstance(value, bool) or value is None:
        return ("v", value)
    return ("s", str(value).strip().casefold())


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
    if not all(isinstance(item, list) for item in (columns, other_columns, rows, other_rows)):
        return False
    names = [str(name).strip().casefold() for name in columns]
    other_names = [str(name).strip().casefold() for name in other_columns]
    if sorted(names) != sorted(other_names) or len(set(names)) != len(names):
        return False
    order = [other_names.index(name) for name in names]
    try:
        reordered = [[row[i] for i in order] for row in other_rows]
    except (IndexError, TypeError):
        return False
    as_keys = lambda table: Counter(tuple(_key(cell) for cell in row) for row in table)  # noqa: E731
    return as_keys(rows) == as_keys(reordered)


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
        same = Counter(map(_key, value)) == Counter(map(_key, other))
    elif kind == "table":
        same = _same_table(value, other)
    else:
        same = _same_scalar(str(kind), value, other)
    return "same" if same else "changed"
