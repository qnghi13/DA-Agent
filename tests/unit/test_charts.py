"""Chart specs: the model names a mark and columns, and code builds Vega-Lite from a query result.

The model never writes Vega-Lite, so a spec cannot carry a data URL, an expression, or numbers that
no query produced.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest

from da_agent.agent.answer import QueryRecord
from da_agent.agent.charts import ChartError, analysis_chart, chart_spec, with_analysis_charts
from da_agent.storage.sqlite_dataset import QueryResult

MONTHLY = QueryResult(
    columns=["month", "revenue", "channel"],
    rows=[["2026-01", 100, "web"], ["2026-02", 250, "web"], ["2026-02", 80, "shop"]],
    truncated=False,
)


def spec(arguments: dict[str, Any], result: QueryResult = MONTHLY) -> dict[str, Any]:
    return chart_spec({"sql": "SELECT 1", **arguments}, result)


def test_a_bar_chart_holds_the_query_rows_and_typed_encodings() -> None:
    built = spec({"mark": "bar", "x": "channel", "y": "revenue", "title": "Revenue by channel"})

    assert built["$schema"].startswith("https://vega.github.io/schema/vega-lite/v6")
    assert built["title"] == "Revenue by channel"
    assert built["mark"]["type"] == "bar"
    assert built["data"]["values"][1] == {"month": "2026-02", "revenue": 250, "channel": "web"}
    assert built["encoding"]["x"] == {"field": "channel", "type": "nominal", "sort": None}
    assert built["encoding"]["y"] == {"field": "revenue", "type": "quantitative"}


def test_date_text_is_temporal_and_color_splits_series() -> None:
    built = spec({"mark": "line", "x": "month", "y": "revenue", "color": "channel"})

    assert built["encoding"]["x"]["type"] == "temporal"
    assert built["encoding"]["color"] == {"field": "channel", "type": "nominal"}


def test_a_pie_uses_the_value_as_angle_and_the_label_as_color() -> None:
    built = spec({"mark": "arc", "x": "channel", "y": "revenue"})

    assert built["encoding"] == {
        "theta": {"field": "revenue", "type": "quantitative"},
        "color": {"field": "channel", "type": "nominal"},
    }


def test_database_values_become_plain_json() -> None:
    result = QueryResult(
        columns=["day", "amount"],
        rows=[[dt.date(2026, 3, 1), Decimal("12.50")]],
        truncated=False,
    )

    built = spec({"mark": "bar", "x": "day", "y": "amount"}, result)

    assert built["data"]["values"] == [{"day": "2026-03-01", "amount": 12.5}]
    assert built["encoding"]["x"]["type"] == "temporal"


def test_field_names_with_dots_and_brackets_are_escaped() -> None:
    # Vega-Lite reads "a.b" as a nested field, so a column named that way must be escaped.
    result = QueryResult(columns=["giá.bán [VND]", "n"], rows=[["x", 1]], truncated=False)

    built = spec({"mark": "bar", "x": "giá.bán [VND]", "y": "n"}, result)

    assert built["encoding"]["x"]["field"] == "giá\\.bán \\[VND\\]"
    assert built["data"]["values"] == [{"giá.bán [VND]": "x", "n": 1}]


@pytest.mark.parametrize(
    ("arguments", "result", "message"),
    [
        ({"mark": "radar", "x": "channel", "y": "revenue"}, MONTHLY, "mark"),
        ({"mark": "bar", "x": "store", "y": "revenue"}, MONTHLY, "store"),
        ({"mark": "bar", "x": "channel", "y": "revenue", "color": "zone"}, MONTHLY, "zone"),
        ({"mark": "bar", "x": "revenue", "y": "channel"}, MONTHLY, "numeric"),
        ({"mark": "bar", "y": "revenue"}, MONTHLY, "x"),
        (
            {"mark": "bar", "x": "a", "y": "b"},
            QueryResult(columns=["a", "b"], rows=[], truncated=False),
            "no rows",
        ),
        (
            {"mark": "bar", "x": "a", "y": "b"},
            QueryResult(columns=["a", "b"], rows=[["x", 1]], truncated=True),
            "aggregate",
        ),
    ],
)
def test_unusable_arguments_are_explained(
    arguments: dict[str, Any], result: QueryResult, message: str
) -> None:
    with pytest.raises(ChartError, match=message):
        spec(arguments, result)


@pytest.mark.security
def test_the_spec_keeps_only_what_code_builds() -> None:
    # Keys the model adds, such as a data URL or a transform with an expression, never reach the
    # spec. Text such as the title stays plain data.
    built = spec(
        {
            "mark": "bar",
            "x": "channel",
            "y": "revenue",
            "title": "<img src=x onerror=alert(1)>" + "x" * 500,
            "data": {"url": "https://evil.example/steal"},
            "transform": [{"calculate": "alert(1)", "as": "z"}],
            "encoding": {"href": {"field": "channel"}},
        }
    )

    assert set(built) == {"$schema", "title", "data", "mark", "encoding"}
    assert set(built["data"]) == {"values"}
    assert set(built["encoding"]) == {"x", "y"}
    assert len(built["title"]) <= 120


def test_months_and_days_get_a_time_unit_so_ticks_fall_on_them() -> None:
    days = QueryResult(
        columns=["day", "n"], rows=[["2026-01-01", 1], ["2026-01-02", 2]], truncated=False
    )

    by_month = spec({"mark": "line", "x": "month", "y": "revenue"})
    by_day = spec({"mark": "line", "x": "day", "y": "n"}, days)

    assert by_month["encoding"]["x"]["timeUnit"] == "yearmonth"
    assert by_day["encoding"]["x"]["timeUnit"] == "yearmonthdate"


@pytest.mark.parametrize(
    "cells",
    [
        # DATE_TRUNC returns a timestamp; the sandbox sends it as text, a local engine as datetime.
        ["2026-04-01 00:00:00", "2026-05-01 00:00:00", "2026-06-01 00:00:00"],
        [dt.datetime(2026, 4, 1), dt.datetime(2026, 5, 1), dt.datetime(2026, 6, 1)],
        ["2026-04-01", "2026-05-01", "2026-06-01"],
    ],
)
def test_month_starts_are_months_not_days_or_timestamps(cells: list[Any]) -> None:
    months = QueryResult(
        columns=["month", "revenue"],
        rows=[[cell, 100 * i] for i, cell in enumerate(cells)],
        truncated=False,
    )

    built = spec({"mark": "line", "x": "month", "y": "revenue"}, months)

    assert built["encoding"]["x"]["type"] == "temporal"
    assert built["encoding"]["x"]["timeUnit"] == "yearmonth"
    assert [v["month"] for v in built["data"]["values"]] == [
        "2026-04-01",
        "2026-05-01",
        "2026-06-01",
    ]


def test_bars_over_months_sit_on_their_month_and_axes_keep_the_column_name() -> None:
    months = QueryResult(
        columns=["month", "revenue"],
        rows=[["2026-04-01 00:00:00", 9], ["2026-05-01 00:00:00", 8]],
        truncated=False,
    )

    bars = spec({"mark": "bar", "x": "month", "y": "revenue"}, months)
    line = spec({"mark": "line", "x": "month", "y": "revenue"}, months)

    # One band per month, labelled by it, rather than a bar from one month's tick to the next.
    assert bars["encoding"]["x"] == {
        "field": "month",
        "type": "ordinal",
        "timeUnit": "yearmonth",
        "title": "month",
    }
    assert line["encoding"]["x"]["type"] == "temporal"
    assert line["encoding"]["x"]["title"] == "month"


def test_midnight_timestamps_on_other_days_are_days() -> None:
    days = QueryResult(
        columns=["day", "n"],
        rows=[["2026-01-01 00:00:00", 1], ["2026-01-02 00:00:00", 2]],
        truncated=False,
    )

    built = spec({"mark": "line", "x": "day", "y": "n"}, days)

    assert built["encoding"]["x"]["timeUnit"] == "yearmonthdate"
    assert built["data"]["values"][1]["day"] == "2026-01-02"


def test_timestamps_with_a_time_of_day_keep_it() -> None:
    hours = QueryResult(
        columns=["at", "n"],
        rows=[["2026-01-01 09:00:00", 1], ["2026-01-01 10:30:00", 2]],
        truncated=False,
    )

    built = spec({"mark": "line", "x": "at", "y": "n"}, hours)

    assert built["encoding"]["x"]["type"] == "temporal"
    assert "timeUnit" not in built["encoding"]["x"]
    assert built["data"]["values"][1]["at"] == "2026-01-01 10:30:00"


def test_labels_keep_the_order_of_the_query_rows() -> None:
    built = spec({"mark": "bar", "x": "channel", "y": "revenue"})

    assert built["encoding"]["x"]["sort"] is None
    assert "sort" not in spec({"mark": "line", "x": "month", "y": "revenue"})["encoding"]["x"]


# Charts from fixed analyses (ADR 0051)

CHANGE = QueryRecord(
    sql="SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL",
    columns=["dimension", "segment", "before", "after", "change", "share_percent", "status"],
    rows=[
        ["(total)", "(all)", 6000.0, 4650.0, -1350.0, 100.0, "both"],
        ["branch", "Q1", 3000.0, 1550.0, -1450.0, 107.4, "both"],
        ["branch", "Q3", 3000.0, 3100.0, 100.0, -7.4, "both"],
        ["category", "Đồ uống", 4000.0, 2500.0, -1500.0, 111.1, "both"],
    ],
    analysis={
        "kind": "explain_change",
        "arguments": {"x": "day", "y": "revenue", "dimensions": ["category", "branch"]},
        "summary": {
            "before": {"start": "2026-09-01", "end": "2026-09-30", "total": 6000.0},
            "after": {"start": "2026-10-01", "end": "2026-10-31", "total": 4650.0},
            "change": -1350.0,
            "dimensions": ["branch", "category"],
        },
        "notes": [],
    },
)


def analysis_record(kind: str, columns: list[str], rows: list[list[Any]]) -> QueryRecord:
    return QueryRecord(
        sql="SELECT m, v FROM t",
        columns=columns,
        rows=rows,
        analysis={"kind": kind, "arguments": {"x": "m", "y": "v"}, "summary": {}, "notes": []},
    )


def test_explain_change_waterfall_starts_and_ends_at_the_totals() -> None:
    chart = analysis_chart(CHANGE)

    assert chart is not None
    values = chart["data"]["values"]
    # Whole months read as the month.
    assert [v["step"] for v in values] == ["2026-09", "Q1", "Q3", "2026-10"]
    assert values[0]["end"] == 6000.0 and values[-1]["end"] == 4650.0
    assert (values[1]["start"], values[1]["end"]) == (6000.0, 4550.0)
    assert (values[2]["start"], values[2]["end"]) == (4550.0, 4650.0)
    assert [v["kind"] for v in values] == ["total", "down", "up", "total"]
    assert chart["title"] == "revenue"


def test_a_waterfall_zooms_so_small_changes_show() -> None:
    chart = analysis_chart(CHANGE)

    assert chart is not None
    scale = chart["layer"][0]["encoding"]["y"]["scale"]
    floor, top = scale["domain"]
    # The axis sits around the levels, not at 0, so a change of 100 on 6000 is visible.
    assert scale["zero"] is False and 0 < floor < 4550.0 and top >= 6000.0


def test_a_range_that_is_not_a_whole_month_keeps_its_dates() -> None:
    record = QueryRecord(
        sql=CHANGE.sql,
        columns=CHANGE.columns,
        rows=CHANGE.rows,
        analysis={
            **(CHANGE.analysis or {}),
            "summary": {
                **(CHANGE.analysis or {})["summary"],
                "before": {"start": "2026-10-01", "end": "2026-10-26", "total": 6000.0},
                "after": {"start": "2026-11-01", "end": "2026-11-26", "total": 4650.0},
            },
        },
    )

    chart = analysis_chart(record)

    assert chart is not None
    assert chart["data"]["values"][0]["step"] == "2026-10-01 to 2026-10-26"


def test_trend_chart_has_the_series_and_the_fitted_line() -> None:
    record = analysis_record("trend", ["m", "v", "fitted"],
                             [["2026-01-01", 10.0, 9.5], ["2026-02-01", 12.0, 12.5]])  # fmt: skip

    chart = analysis_chart(record)

    assert chart is not None
    fields = [layer["encoding"]["y"]["field"] for layer in chart["layer"]]
    assert fields == ["v", "fitted"]
    assert chart["data"]["values"][1] == {"m": "2026-02-01", "v": 12.0, "fitted": 12.5}


def test_forecast_chart_has_history_forecast_and_band() -> None:
    columns = ["m", "v", "forecast", "low_95", "high_95"]
    rows = [["2026-09-01", 10.0, None, None, None], ["2026-10-01", None, 11.0, 9.0, 13.0]]

    chart = analysis_chart(analysis_record("forecast", columns, rows))

    assert chart is not None
    marks = [layer["mark"]["type"] for layer in chart["layer"]]
    assert marks == ["area", "line", "line"]
    assert chart["layer"][0]["encoding"]["y2"]["field"] == "high_95"


@pytest.mark.parametrize("kind", ["correlation", "outliers"])
def test_no_chart_for_correlation_or_outliers(kind: str) -> None:
    assert analysis_chart(analysis_record(kind, ["m", "v"], [["a", 1]])) is None


def test_analysis_charts_hold_no_model_text() -> None:
    sneaky = QueryRecord(
        sql=CHANGE.sql,
        columns=CHANGE.columns,
        rows=CHANGE.rows,
        analysis={
            **(CHANGE.analysis or {}),
            "arguments": {"y": "revenue", "title": "<script>", "transform": "x"},
        },
    )

    chart = analysis_chart(sneaky)

    text = json.dumps(chart)
    assert "<script>" not in text and "transform" not in text and "url" not in text


def test_charts_are_attached_to_analysis_records_only() -> None:
    plain = QueryRecord(sql="SELECT 1", columns=["n"], rows=[[1]])

    attached = with_analysis_charts([plain, CHANGE])

    assert attached[0] == plain
    assert attached[1].analysis is not None and attached[1].analysis["chart"]["layer"]


def test_waterfall_totals_are_marks_not_bars_from_a_cut_axis() -> None:
    chart = analysis_chart(CHANGE)

    assert chart is not None
    totals = [v for v in chart["data"]["values"] if v["kind"] == "total"]
    # A total has no bar length to misread: it is a mark at its value, with the value written.
    assert all(v["start"] == v["end"] for v in totals)
    marks = [layer["mark"]["type"] for layer in chart["layer"]]
    assert marks == ["bar", "tick", "text"]
