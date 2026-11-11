"""Periods relative to today are computed by code, so the model never does date arithmetic."""

import datetime as dt

import pytest

from da_agent.agent.periods import reference_periods
from da_agent.agent.planning import plan_user_prompt
from da_agent.agent.prompts import build_user_prompt


def _periods(as_of: str) -> dict[str, tuple[str, str]]:
    return {
        p.name: (p.start.isoformat(), p.end.isoformat())
        for p in reference_periods(dt.date.fromisoformat(as_of))
    }


def test_today_and_yesterday() -> None:
    periods = _periods("2026-04-14")
    assert periods["today"] == ("2026-04-14", "2026-04-14")
    assert periods["yesterday"] == ("2026-04-13", "2026-04-13")


@pytest.mark.parametrize(
    ("as_of", "this_week", "last_week"),
    [
        # Tuesday
        ("2025-12-09", ("2025-12-08", "2025-12-09"), ("2025-12-01", "2025-12-07")),
        # Saturday
        ("2025-08-16", ("2025-08-11", "2025-08-16"), ("2025-08-04", "2025-08-10")),
        # Monday: this week is today alone
        ("2025-08-18", ("2025-08-18", "2025-08-18"), ("2025-08-11", "2025-08-17")),
        # Sunday
        ("2025-08-17", ("2025-08-11", "2025-08-17"), ("2025-08-04", "2025-08-10")),
    ],
)
def test_weeks_start_on_monday(
    as_of: str, this_week: tuple[str, str], last_week: tuple[str, str]
) -> None:
    periods = _periods(as_of)
    assert periods["this week"] == this_week
    assert periods["last week"] == last_week


def test_last_n_days_include_today() -> None:
    periods = _periods("2025-06-21")
    assert periods["last 7 days"] == ("2025-06-15", "2025-06-21")
    assert periods["last 30 days"] == ("2025-05-23", "2025-06-21")


def test_month_to_date_and_the_same_days_of_last_month() -> None:
    periods = _periods("2026-06-08")
    assert periods["this month"] == ("2026-06-01", "2026-06-08")
    assert periods["last month, same days"] == ("2026-05-01", "2026-05-08")
    assert periods["last month"] == ("2026-05-01", "2026-05-31")


def test_same_days_of_a_shorter_last_month_stop_at_its_end() -> None:
    periods = _periods("2026-03-31")
    assert periods["last month, same days"] == ("2026-02-01", "2026-02-28")


def test_january_looks_back_to_december() -> None:
    periods = _periods("2026-01-10")
    assert periods["last month"] == ("2025-12-01", "2025-12-31")
    assert periods["last quarter"] == ("2025-10-01", "2025-12-31")
    assert periods["last year"] == ("2025-01-01", "2025-12-31")


def test_quarters_and_years() -> None:
    periods = _periods("2026-08-14")
    assert periods["this quarter"] == ("2026-07-01", "2026-08-14")
    assert periods["last quarter"] == ("2026-04-01", "2026-06-30")
    assert periods["this year"] == ("2026-01-01", "2026-08-14")


def test_the_agent_and_the_plan_see_the_resolved_periods() -> None:
    for prompt in (
        build_user_prompt("Tuần trước bán được bao nhiêu?", "2025-12-09"),
        plan_user_prompt("Tuần trước bán được bao nhiêu?", "2025-12-09", [], []),
    ):
        assert "2025-12-01" in prompt
        assert "2025-12-07" in prompt


def test_an_unreadable_date_leaves_the_prompt_without_periods() -> None:
    prompt = build_user_prompt("Revenue?", "not a date")
    assert "not a date" in prompt
