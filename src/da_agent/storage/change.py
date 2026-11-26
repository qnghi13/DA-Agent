"""explain_change: where a sum or count changed between two periods, split by segment.

See docs/adr/0051. Code sums y over the rows in each date range and splits the change by every
value of each dimension. The parts of one dimension add up to the change exactly, so the answer
can say "Q1 carries 68% of the drop". That says where the change happened, never why.

The checks refuse what would mislead: a range that runs past the data (part of a month against a
whole one), a range that cuts a period of x in two, and a range with no rows.
"""

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from da_agent.storage.stats import (
    StatsError,
    cell,
    days_of,
    grain_of,
    note,
    numbers,
    rounded,
    table,
    without_missing_dates,
)

TOP_SEGMENTS = 10
# A change smaller than this share of the before total counts as no change.
FLAT_SHARE = 0.01
TOTAL = "(total)"
ALL = "(all)"
OTHER = "(other)"
BLANK = "(blank)"
COLUMNS = ["dimension", "segment", "before", "after", "change", "share_percent", "status"]
METHOD = (
    "sum of y in each date range, split by every value of each dimension; the parts of one "
    "dimension add up to the change"
)

Span = tuple[dt.date, dt.date]


def explain_change(
    frame: pd.DataFrame,
    x: str,
    y: str,
    dimensions: Sequence[str],
    before: Span,
    after: Span,
    *,
    data_start: dt.date | None,
    data_end: dt.date | None,
) -> dict[str, Any]:
    """Return {"kind", "summary", "table", "notes"}, or raise StatsError naming the fix."""
    for name in (x, y, *dimensions):
        if name not in frame.columns:
            raise StatsError(f"no column {name!r}; the query returns {list(frame.columns)}")
    for span in (before, after):
        if span[0] > span[1]:
            raise StatsError(f"a range must start on or before its end: {_shown(span)}")
    if before[1] >= after[0]:
        raise StatsError("before must end before after starts")
    frame, notes = without_missing_dates(frame, x)
    try:
        days = days_of(frame, x)
    except StatsError as error:
        raise StatsError(
            "x must hold dates, such as CAST(order_date AS DATE); group x by day to compare "
            "any two ranges"
        ) from error
    distinct = sorted(set(days))
    grain = grain_of(distinct) if len(distinct) > 1 else "day"
    if grain != "day":
        for span in (before, after):
            _check_whole_periods(span, grain, distinct[0])
    low = data_start or (distinct[0] if distinct else None)
    high = data_end or (distinct[-1] if distinct else None)
    if data_start is None and data_end is None and low is not None and high is not None:
        notes.append(
            note(
                "coverage_unknown",
                f"the data's date range is unknown, so x's own range, {low} to {high}, was used",
                start=low.isoformat(),
                end=high.isoformat(),
            )
        )
    if low is not None and high is not None:
        for span in (before, after):
            _check_coverage(span, low, high)
    _check_same_length(before, after)
    values = numbers(frame, y)
    dates = np.array(days, dtype=object)
    in_before = (dates >= before[0]) & (dates <= before[1])
    in_after = (dates >= after[0]) & (dates <= after[1])
    for span, mask in ((before, in_before), (after, in_after)):
        if not mask.any():
            raise StatsError(f"no rows between {span[0]} and {span[1]}")
    missing = int(np.isnan(values[in_before | in_after]).sum())
    if missing:
        text = f"{missing} rows in the ranges have no {y} value; they count as 0"
        notes.append(note("missing_values", text, count=missing, column=y))
    amounts = np.nan_to_num(values)
    before_total = float(amounts[in_before].sum())
    after_total = float(amounts[in_after].sum())
    total_change = after_total - before_total
    if before_total == 0:
        flat = total_change == 0
        notes.append(note("before_zero", "the before total is 0, so there is no percent change"))
    else:
        flat = abs(total_change) < FLAT_SHARE * abs(before_total)
    if flat:
        notes.append(
            note("flat_total", "the total barely moved; segments offset each other, so no shares")
        )
    splits = {
        name: _segments(pd.Series(frame[name]), amounts, in_before, in_after, total_change, flat)
        for name in dimensions
    }
    ranked = sorted(dimensions, key=lambda name: -_score(splits[name], flat))
    rows: list[list[Any]] = [
        [
            TOTAL,
            ALL,
            rounded(before_total),
            rounded(after_total),
            rounded(total_change),
            None if flat else 100.0,
            "both",
        ]
    ]
    for name in ranked:
        rows += _rows(name, splits[name], total_change, flat)
    top = None
    if not flat and ranked:
        best = max(splits[ranked[0]], key=lambda s: s["share"])
        top = {
            "dimension": ranked[0],
            "segment": best["segment"],
            "change": rounded(best["change"]),
            "share_percent": rounded(best["share"], 1),
        }
    summary = {
        "method": METHOD,
        "grain": grain,
        "before": {**_iso(before), "total": rounded(before_total)},
        "after": {**_iso(after), "total": rounded(after_total)},
        "change": rounded(total_change),
        "change_percent": (
            rounded(100 * total_change / before_total, 1) if before_total != 0 else None
        ),
        "flat": flat,
        "dimensions": ranked,
        "top": top,
    }
    return {
        "kind": "explain_change",
        "summary": summary,
        "table": table(COLUMNS, rows),
        "notes": notes,
    }


def _shown(span: Span) -> str:
    return f"{span[0]} to {span[1]}"


def _iso(span: Span) -> dict[str, str]:
    return {"start": span[0].isoformat(), "end": span[1].isoformat()}


def _period_start(day: dt.date, grain: str, anchor: dt.date) -> dt.date:
    """The first day of the period of x that day falls in. anchor is one period start of x."""
    if grain == "week":
        return day - dt.timedelta(days=(day - anchor).days % 7)
    if grain == "month":
        return day.replace(day=1)
    if grain == "quarter":
        return dt.date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    # A year of x starts on the anchor's month.
    year = day.year if day.month >= anchor.month else day.year - 1
    return dt.date(year, anchor.month, 1)


def _check_whole_periods(span: Span, grain: str, anchor: dt.date) -> None:
    start_ok = _period_start(span[0], grain, anchor) == span[0]
    following = span[1] + dt.timedelta(days=1)
    end_ok = _period_start(following, grain, anchor) == following
    if not (start_ok and end_ok):
        raise StatsError(
            f"x is one row per {grain}, so each range must start and end on whole {grain}s, "
            f"and {_shown(span)} does not; group x by day to compare part of a {grain}"
        )


def _whole_months(span: Span) -> int | None:
    """How many calendar months the range covers exactly, or None when it cuts one."""
    start, end = span
    following = end + dt.timedelta(days=1)
    if start.day != 1 or following.day != 1:
        return None
    return (following.year - start.year) * 12 + following.month - start.month


def _check_same_length(before: Span, after: Span) -> None:
    """Two ranges compare only when they are as long, or the same number of whole months: 1-26
    November against all of October would show a drop that is only missing days."""
    days_before = (before[1] - before[0]).days + 1
    days_after = (after[1] - after[0]).days + 1
    if days_before == days_after:
        return
    months = _whole_months(before)
    if months is not None and months == _whole_months(after):
        return
    if days_after < days_before:
        suggested = f"before {before[0]} to {before[0] + dt.timedelta(days=days_after - 1)}"
    else:
        suggested = f"after {after[0]} to {after[0] + dt.timedelta(days=days_before - 1)}"
    raise StatsError(
        f"before has {days_before} days and after has {days_after}, so the change would count "
        f"missing days; compare the same days, such as {suggested}"
    )


def _check_coverage(span: Span, low: dt.date, high: dt.date) -> None:
    if span[1] > high:
        raise StatsError(
            f"the data covers {low} to {high}, and {_shown(span)} runs past it; end the range "
            f"on {high} and compare the same days of the other period"
        )
    if span[0] < low:
        raise StatsError(
            f"the data covers {low} to {high}, and {_shown(span)} starts before it; start the "
            f"range on {low} and compare the same days of the other period"
        )


def _segment_name(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return BLANK
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    shown = cell(value)
    text = str(shown).strip() if shown is not None else ""
    return text or BLANK


def _segments(
    column: pd.Series,
    amounts: np.ndarray,
    in_before: np.ndarray,
    in_after: np.ndarray,
    total_change: float,
    flat: bool,
) -> list[dict[str, Any]]:
    """Each value's totals, change, share, and status, largest change first."""
    names = [_segment_name(value) for value in column.tolist()]
    found: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(names):
        if not (in_before[index] or in_after[index]):
            continue
        entry = found.setdefault(
            name, {"segment": name, "before": 0.0, "after": 0.0, "rows_before": 0, "rows_after": 0}
        )
        if in_before[index]:
            entry["before"] += float(amounts[index])
            entry["rows_before"] += 1
        if in_after[index]:
            entry["after"] += float(amounts[index])
            entry["rows_after"] += 1
    segments = []
    for entry in found.values():
        moved = entry["after"] - entry["before"]
        status = (
            "new" if not entry["rows_before"] else "gone" if not entry["rows_after"] else "both"
        )
        share = 100 * moved / total_change if not flat and total_change != 0 else None
        segments.append({**entry, "change": moved, "share": share, "status": status})
    return sorted(segments, key=lambda s: (-abs(s["change"]), s["segment"]))


def _score(segments: list[dict[str, Any]], flat: bool) -> float:
    """How much of the change one dimension explains: its largest share, or when the total is
    flat, how much its segments moved in all."""
    if len(segments) < 2:
        # One value, such as the branch a query already filtered to, holds all of the change
        # and says nothing: it ranks last.
        return -math.inf
    if flat:
        return sum(abs(s["change"]) for s in segments)
    return max((s["share"] for s in segments), default=0.0)


def _rows(
    name: str, segments: list[dict[str, Any]], total_change: float, flat: bool
) -> list[list[Any]]:
    def row(segment: str, before: float, after: float, status: str) -> list[Any]:
        moved = after - before
        share = None if flat or total_change == 0 else rounded(100 * moved / total_change, 1)
        return [name, segment, rounded(before), rounded(after), rounded(moved), share, status]

    shown = [
        row(s["segment"], s["before"], s["after"], s["status"]) for s in segments[:TOP_SEGMENTS]
    ]
    rest = segments[TOP_SEGMENTS:]
    if rest:
        before = sum(s["before"] for s in rest)
        after = sum(s["after"] for s in rest)
        shown.append(row(OTHER, before, after, "other"))
    return shown
