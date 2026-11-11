"""Periods relative to today, computed by code and given to the model with the question.

Small models get date arithmetic wrong: a week that starts on Sunday, "last week" that ends today,
"this week" that runs into the future. Code computes the common periods once, so the model picks
one instead of deriving it. Weeks start on Monday (ISO 8601). Every range includes both ends.
"""

import calendar
import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Period:
    name: str
    start: dt.date
    end: dt.date


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _previous_month(day: dt.date) -> tuple[int, int]:
    return (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)


def reference_periods(today: dt.date) -> list[Period]:
    day = dt.timedelta(days=1)
    monday = today - dt.timedelta(days=today.weekday())
    year, month = _previous_month(today)
    last_month_start = dt.date(year, month, 1)
    last_month_end = _month_end(year, month)
    quarter_month = 3 * ((today.month - 1) // 3) + 1
    quarter_start = dt.date(today.year, quarter_month, 1)
    last_quarter_end = quarter_start - day
    last_quarter_start = dt.date(last_quarter_end.year, last_quarter_end.month - 2, 1)
    return [
        Period("today", today, today),
        Period("yesterday", today - day, today - day),
        Period("this week", monday, today),
        Period("last week", monday - 7 * day, monday - day),
        Period("last 7 days", today - 6 * day, today),
        Period("last 30 days", today - 29 * day, today),
        Period("this month", today.replace(day=1), today),
        Period("last month, same days", last_month_start, min(
            last_month_start.replace(day=min(today.day, last_month_end.day)), last_month_end
        )),
        Period("last month", last_month_start, last_month_end),
        Period("this quarter", quarter_start, today),
        Period("last quarter", last_quarter_start, last_quarter_end),
        Period("this year", dt.date(today.year, 1, 1), today),
        Period("last year", dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)),
    ]  # fmt: skip


def periods_note(as_of: str) -> str:
    """The periods as prompt text, or nothing when as_of is not a date."""
    try:
        today = dt.date.fromisoformat(as_of)
    except ValueError:
        return ""
    lines = "\n".join(
        f"- {p.name}: {p.start.isoformat()} to {p.end.isoformat()}"
        for p in reference_periods(today)
    )
    return (
        f"Today is {today.strftime('%A')}. Periods relative to today, computed by code, both "
        "ends included (weeks start on Monday). Use these dates when the question uses one of "
        f"these periods, unless the documents define it differently:\n{lines}"
    )
