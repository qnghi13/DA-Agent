"""explain_change: where a sum or count changed between two periods, split by segment (ADR 0051).

Frames stand in for a query result. Each segment adds a fixed amount per day in each range, so the
expected totals follow from the number of days.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from da_agent.storage.change import BLANK, COLUMNS, OTHER, TOTAL, explain_change
from da_agent.storage.stats import StatsError, run_stats

SEP = (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
OCT = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
# As many days as September, so a constant daily amount gives the same total in both.
OCT_30 = (dt.date(2026, 10, 1), dt.date(2026, 10, 30))


def days(span: tuple[dt.date, dt.date]) -> list[dt.date]:
    count = (span[1] - span[0]).days + 1
    return [span[0] + dt.timedelta(days=i) for i in range(count)]


def daily(
    segments: dict[tuple, tuple[float, float]],
    before: tuple[dt.date, dt.date],
    after: tuple[dt.date, dt.date],
    dims: tuple[str, ...],
) -> pd.DataFrame:
    """One row per day of each range and segment: (before_per_day, after_per_day) of revenue."""
    rows: list[dict[str, Any]] = []
    for key, (per_day_before, per_day_after) in segments.items():
        for span, amount in ((before, per_day_before), (after, per_day_after)):
            if amount is None:
                continue
            for day in days(span):
                rows.append({"day": day, **dict(zip(dims, key, strict=True)), "revenue": amount})
    return pd.DataFrame(rows)


def change(frame: pd.DataFrame, dims: list[str], before=SEP, after=OCT, **coverage: Any):
    coverage = {"data_start": SEP[0], "data_end": OCT[1], **coverage}
    return explain_change(frame, "day", "revenue", dims, before, after, **coverage)


def rows_of(result: dict[str, Any], dimension: str) -> list[list[Any]]:
    return [row for row in result["table"]["rows"] if row[0] == dimension]


BRANCHES = {("Q1", "Đồ uống"): (300, 100), ("Q3", "Đồ ăn"): (200, 160), ("Q7", "Đồ ăn"): (100, 110)}


def test_the_drop_splits_by_branch_and_sums_to_the_change() -> None:
    frame = daily(BRANCHES, SEP, OCT, dims=("branch", "category"))
    result = change(frame, ["branch", "category"])

    s = result["summary"]
    assert s["before"] == {"start": "2026-09-01", "end": "2026-09-30", "total": 600 * 30}
    assert s["after"] == {"start": "2026-10-01", "end": "2026-10-31", "total": 370 * 31}
    assert s["change"] == pytest.approx(370 * 31 - 600 * 30)
    assert s["change_percent"] == pytest.approx(round(100 * (370 * 31 - 600 * 30) / 18000, 1))
    assert result["table"]["columns"] == COLUMNS
    assert result["table"]["rows"][0][:2] == [TOTAL, "(all)"]
    for dimension in ("branch", "category"):
        assert sum(row[4] for row in rows_of(result, dimension)) == pytest.approx(s["change"])
    assert s["top"]["dimension"] == "branch" and s["top"]["segment"] == "Q1"
    assert s["dimensions"] == ["branch", "category"]
    # Q1 fell most, so it leads its dimension.
    assert [row[1] for row in rows_of(result, "branch")] == ["Q1", "Q3", "Q7"]


def test_offsetting_segments_can_share_more_than_all_of_the_change() -> None:
    frame = daily({("Q1",): (200, 100), ("Q7",): (100, 140)}, SEP, OCT_30, ("branch",))
    result = change(frame, ["branch"], after=OCT_30)

    shares = {row[1]: row[5] for row in rows_of(result, "branch")}
    assert shares["Q1"] > 100
    assert shares["Q7"] < 0
    assert sum(shares.values()) == pytest.approx(100, abs=0.2)


def test_a_flat_total_ranks_by_movement_and_has_no_shares() -> None:
    frame = daily(
        {("Q1", "A"): (100, 150), ("Q3", "A"): (100, 50), ("Q7", "B"): (10, 10)},
        SEP, OCT_30, ("branch", "category"),
    )  # fmt: skip
    result = change(frame, ["category", "branch"], after=OCT_30)

    s = result["summary"]
    assert s["flat"] is True and s["top"] is None
    assert all(row[5] is None for row in result["table"]["rows"])
    assert [n["code"] for n in result["notes"]] == ["flat_total"]
    # Branches moved 1500 each way; categories did not move at all.
    assert s["dimensions"] == ["branch", "category"]


def test_new_and_gone_segments() -> None:
    frame = daily(
        {("Q1",): (100, None), ("Q9",): (None, 50), ("Q3",): (10, 10)}, SEP, OCT, ("branch",)
    )
    result = change(frame, ["branch"])

    status = {row[1]: row[6] for row in rows_of(result, "branch")}
    assert status == {"Q1": "gone", "Q9": "new", "Q3": "both"}


def test_blank_numeric_and_accented_segments() -> None:
    frame = daily(
        {(None,): (10, 20), ("",): (5, 5), (12,): (1, 2), ("Huỷ",): (3, 1)}, SEP, OCT, ("code",)
    )
    result = change(frame, ["code"])

    segments = {row[1] for row in rows_of(result, "code")}
    assert segments == {BLANK, "12", "Huỷ"}
    blank = next(row for row in rows_of(result, "code") if row[1] == BLANK)
    assert blank[2] == (10 + 5) * 30


def test_many_segments_keep_ten_and_other_and_still_sum() -> None:
    products = {(f"P{i:03d}",): (100 + i, 100 + 2 * i) for i in range(500)}
    frame = daily(products, SEP, OCT_30, ("product",))
    result = change(frame, ["product"], after=OCT_30)

    rows = rows_of(result, "product")
    assert len(rows) == 11 and rows[-1][1] == OTHER and rows[-1][6] == "other"
    assert [row[1] for row in rows[:3]] == ["P499", "P498", "P497"]
    assert sum(row[4] for row in rows) == pytest.approx(result["summary"]["change"])
    assert not result["table"]["truncated"]


def test_a_missing_column_is_named() -> None:
    frame = daily(BRANCHES, SEP, OCT, dims=("branch", "category"))
    with pytest.raises(StatsError, match="no column 'channel'"):
        change(frame, ["branch", "channel"])


def test_before_must_end_before_after_starts() -> None:
    frame = daily(BRANCHES, SEP, OCT, dims=("branch", "category"))
    with pytest.raises(StatsError, match="before must end before after starts"):
        change(frame, ["branch"], before=OCT, after=SEP)


def test_x_must_hold_dates() -> None:
    frame = pd.DataFrame({"day": ["a", "b"], "branch": ["Q1", "Q1"], "revenue": [1, 2]})
    with pytest.raises(StatsError, match="x must hold dates"):
        change(frame, ["branch"])


def test_a_monthly_x_cannot_compare_part_of_a_month() -> None:
    frame = pd.DataFrame(
        {
            "month": [dt.date(2026, 10, 1), dt.date(2026, 11, 1)],
            "branch": ["Q1", "Q1"],
            "revenue": [100.0, 60.0],
        }
    )
    with pytest.raises(StatsError, match="one row per month") as error:
        explain_change(frame, "month", "revenue", ["branch"],
                       (dt.date(2026, 10, 1), dt.date(2026, 10, 26)),
                       (dt.date(2026, 11, 1), dt.date(2026, 11, 26)),
                       data_start=dt.date(2026, 10, 1), data_end=dt.date(2026, 11, 26))  # fmt: skip
    assert "group x by day" in str(error.value)


def test_whole_months_work_with_a_monthly_x() -> None:
    frame = pd.DataFrame(
        {
            "month": [dt.date(2026, 9, 1), dt.date(2026, 10, 1)],
            "branch": ["Q1", "Q1"],
            "revenue": [100.0, 60.0],
        }
    )
    result = explain_change(frame, "month", "revenue", ["branch"], SEP, OCT,
                            data_start=SEP[0], data_end=OCT[1])  # fmt: skip
    assert result["summary"]["grain"] == "month"
    assert result["summary"]["change"] == pytest.approx(-40)


def test_a_range_past_the_data_is_refused_with_the_same_days_hint() -> None:
    nov = (dt.date(2026, 11, 1), dt.date(2026, 11, 26))
    frame = daily({("Q1",): (100, 90)}, OCT, (nov[0], dt.date(2026, 11, 20)), ("branch",))
    with pytest.raises(StatsError) as error:
        change(frame, ["branch"], before=(OCT[0], dt.date(2026, 10, 26)), after=nov,
               data_start=OCT[0], data_end=dt.date(2026, 11, 20))  # fmt: skip
    message = str(error.value)
    assert "2026-11-20" in message and "same days" in message


def test_unknown_coverage_uses_x_and_notes_it() -> None:
    frame = daily({("Q1",): (100, 90)}, SEP, OCT, ("branch",))
    result = change(frame, ["branch"], data_start=None, data_end=None)

    assert result["notes"] == [
        {
            "code": "coverage_unknown",
            "params": {"start": "2026-09-01", "end": "2026-10-31"},
            "text": "the data's date range is unknown, so x's own range, 2026-09-01 to "
            "2026-10-31, was used",
        }
    ]


def test_a_range_without_rows_is_refused() -> None:
    frame = daily({("Q1",): (100, None)}, SEP, OCT, ("branch",))
    frame = pd.concat([frame, pd.DataFrame([{"day": OCT[1], "branch": "Q1", "revenue": 1}])])
    with pytest.raises(StatsError, match="no rows between 2026-10-01 and 2026-10-30"):
        change(frame, ["branch"], after=OCT_30)


def test_a_zero_before_total_has_no_percent() -> None:
    frame = daily({("Q1",): (0, 10)}, SEP, OCT, ("branch",))
    result = change(frame, ["branch"])

    assert result["summary"]["change_percent"] is None
    assert "before_zero" in [n["code"] for n in result["notes"]]
    assert result["summary"]["top"]["segment"] == "Q1"


def test_run_stats_reads_the_query_and_explains_the_change(tmp_path: Path) -> None:
    path = tmp_path / "dataset.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE orders AS SELECT DATE '2026-09-01' + i::INT AS day, "
            "CASE WHEN i % 2 = 0 THEN 'Q1' ELSE 'Q3' END AS branch, "
            "CASE WHEN i >= 30 AND i % 2 = 0 THEN 50 ELSE 100 END AS amount FROM range(61) r(i)"
        )
    sql = "SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL"
    result = run_stats(path, "explain_change", sql, "day", "revenue", data_start=SEP[0],
                       data_end=OCT[1], dimensions=["branch"], before=SEP, after=OCT)  # fmt: skip

    assert result["kind"] == "explain_change"
    assert result["summary"]["top"]["segment"] == "Q1"
    assert result["summary"]["change"] < 0


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"dimensions": ["branch"], "before": SEP}, "before and after"),
        ({"dimensions": [], "before": SEP, "after": OCT}, "1 to 4 dimensions"),
        ({"dimensions": ["a", "b", "c", "d", "e"], "before": SEP, "after": OCT}, "1 to 4"),
    ],
)
def test_run_stats_needs_ranges_and_dimensions(tmp_path: Path, options, message) -> None:
    with pytest.raises(StatsError, match=message):
        run_stats(tmp_path / "unused.duckdb", "explain_change", "SELECT 1", "day", "revenue",
                  data_start=None, data_end=None, **options)  # fmt: skip


# Review fixes (2026-11-26)

NOV_26 = (dt.date(2026, 11, 1), dt.date(2026, 11, 26))
OCT_26 = (dt.date(2026, 10, 1), dt.date(2026, 10, 26))


def test_part_of_a_month_against_a_whole_month_is_refused_with_the_same_days() -> None:
    frame = daily({("Q1",): (100, 90)}, OCT, NOV_26, ("branch",))
    with pytest.raises(StatsError) as error:
        change(frame, ["branch"], before=OCT, after=NOV_26, data_end=NOV_26[1])

    message = str(error.value)
    assert "2026-10-01 to 2026-10-26" in message and "same days" in message


def test_ranges_of_different_lengths_inside_the_data_are_refused() -> None:
    nov_20 = (dt.date(2026, 11, 1), dt.date(2026, 11, 20))
    frame = daily({("Q1",): (100, 90)}, OCT, nov_20, ("branch",))
    with pytest.raises(StatsError, match="2026-10-01 to 2026-10-20"):
        change(frame, ["branch"], before=OCT_26, after=nov_20, data_end=nov_20[1])


def test_same_days_and_whole_months_are_allowed() -> None:
    frame = daily({("Q1",): (100, 90)}, OCT_26, NOV_26, ("branch",))
    assert change(frame, ["branch"], before=OCT_26, after=NOV_26, data_end=NOV_26[1])
    frame = daily({("Q1",): (100, 90)}, SEP, OCT, ("branch",))
    assert change(frame, ["branch"])  # 30 against 31 days, both whole months


def test_whole_quarters_of_different_lengths_are_allowed() -> None:
    q1, q2 = (
        (dt.date(2026, 1, 1), dt.date(2026, 3, 31)),
        (dt.date(2026, 4, 1), dt.date(2026, 6, 30)),
    )
    frame = daily({("Q1",): (100, 90)}, q1, q2, ("branch",))
    result = change(frame, ["branch"], before=q1, after=q2, data_start=q1[0], data_end=q2[1])
    assert result["summary"]["change"] == pytest.approx(90 * 91 - 100 * 90)


def test_a_dimension_with_one_value_ranks_last() -> None:
    frame = daily(
        {("Q1", "Đồ ăn"): (300, 200), ("Q1", "Đồ uống"): (100, 50)},
        SEP,
        OCT_30,
        ("branch", "category"),
    )
    result = change(frame, ["branch", "category"], after=OCT_30)

    assert result["summary"]["dimensions"] == ["category", "branch"]
    assert result["summary"]["top"]["dimension"] == "category"


def test_rows_without_a_date_are_left_out_and_noted() -> None:
    frame = daily({("Q1",): (100, 90)}, SEP, OCT, ("branch",))
    frame = pd.concat([frame, pd.DataFrame([{"day": None, "branch": "Q1", "revenue": 5000}])])

    result = change(frame, ["branch"])

    assert result["summary"]["before"]["total"] == 100 * 30
    assert {k: v for k, v in result["notes"][0].items() if k != "text"} == {
        "code": "missing_dates",
        "params": {"count": 1, "column": "day"},
    }
