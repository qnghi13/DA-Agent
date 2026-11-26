"""Fixed analyses (ADR 0051): code picks the method, the model only names a query and columns.

These run in process on small DuckDB files; in production the same module runs in the sandbox.
"""

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from da_agent.storage.stats import StatsError, run_stats


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Daily sales from 2025-01-01 to 2025-12-10: daily revenue grows by 30, so a month's total
    grows by about 30 x 30.4 x 30.4, with three spikes and a quantity that falls as price rises."""
    path = tmp_path / "dataset.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE sales AS
            SELECT DATE '2025-01-01' + i::INT AS day,
                   1000 + 30 * i + CASE WHEN i IN (40, 200, 300) THEN 20000 ELSE 0 END
                       AS revenue,
                   10 + (i % 7) AS price,
                   100 - 5 * (i % 7) + (i % 3) AS quantity
            FROM range(344) AS r(i)
            """
        )
    return path


MONTHLY = (
    "SELECT date_trunc('month', day) AS month, sum(revenue) AS revenue FROM sales "
    "GROUP BY 1 ORDER BY 1"
)
DATA = (dt.date(2025, 1, 1), dt.date(2025, 12, 10))


def stats(database: Path, kind: str, sql: str = MONTHLY, x: str = "month", y: str = "revenue",
          **options):  # fmt: skip
    return run_stats(database, kind, sql, x, y, data_start=DATA[0], data_end=DATA[1], **options)


def test_a_trend_leaves_out_the_unfinished_last_month_and_fits_a_line(database: Path) -> None:
    result = stats(database, "trend")

    summary = result["summary"]
    assert summary["periods"] == 11 and summary["grain"] == "month"
    assert summary["direction"] == "increasing"
    assert 22_000 < summary["slope_per_period"] < 33_000
    assert summary["p_value"] < 0.001
    assert any(
        note["code"] == "unfinished_last" and note["params"]["period"] == "2025-12"
        for note in result["notes"]
    )
    assert result["table"]["columns"] == ["month", "revenue", "fitted"]
    assert result["table"]["rows"][-1][0] == "2025-11-01"


def test_a_flat_noisy_series_has_no_clear_direction(tmp_path: Path) -> None:
    path = tmp_path / "flat.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE t AS SELECT DATE '2025-01-01' + INTERVAL (i) MONTH AS m, "
            "[100, 130, 90, 120, 95, 125, 105, 98][i + 1] AS v FROM range(8) r(i)"
        )
    result = run_stats(path, "trend", "SELECT m, v FROM t", "m", "v",
                       data_start=dt.date(2025, 1, 1), data_end=dt.date(2025, 8, 31))  # fmt: skip

    assert result["summary"]["direction"] == "no clear trend"


def test_a_forecast_continues_the_trend_with_an_interval(database: Path) -> None:
    result = stats(database, "forecast", periods=2)

    rows = [row for row in result["table"]["rows"] if row[1] is None]
    assert result["table"]["columns"] == ["month", "revenue", "forecast", "low_95", "high_95"]
    assert [r[0] for r in rows] == ["2025-12-01", "2026-01-01"]
    rows = [[r[0], r[2], r[3], r[4]] for r in rows]
    last_full = 1000 + 30 * 318  # mid-November, about the November daily level
    assert rows[0][1] > 30 * last_full * 0.9
    assert rows[0][2] < rows[0][1] < rows[0][3]
    assert "method" in result["summary"]


def test_correlation_reports_pearson_and_spearman_over_all_rows(database: Path) -> None:
    result = run_stats(database, "correlation", "SELECT price, quantity FROM sales",
                       "price", "quantity", data_start=DATA[0], data_end=DATA[1])  # fmt: skip

    summary = result["summary"]
    assert summary["rows"] == 344
    assert summary["pearson"] < -0.95 and summary["spearman"] < -0.95
    assert summary["p_value"] < 0.001


def test_outliers_use_a_robust_score_and_list_the_spikes(database: Path) -> None:
    sql = "SELECT day, revenue FROM sales"
    result = run_stats(database, "outliers", sql, "day", "revenue",
                       data_start=DATA[0], data_end=DATA[1])  # fmt: skip

    days = [row[0] for row in result["table"]["rows"]]
    assert days[:3] == ["2025-10-28", "2025-07-20", "2025-02-10"]
    assert result["summary"]["outliers"] == 3
    assert result["table"]["columns"] == ["day", "revenue", "robust_z", "side"]


def test_x_must_be_regular_periods_for_a_trend(database: Path) -> None:
    with pytest.raises(StatsError, match="date_trunc"):
        run_stats(database, "trend", "SELECT revenue AS r, revenue FROM sales LIMIT 9", "r",
                  "revenue", data_start=DATA[0], data_end=DATA[1])  # fmt: skip


def test_unknown_columns_and_too_few_points_are_explained(database: Path) -> None:
    with pytest.raises(StatsError, match="no column 'sales'"):
        stats(database, "trend", y="sales")
    short = "SELECT date_trunc('month', day) AS month, sum(revenue) AS revenue FROM sales " \
            "WHERE day < DATE '2025-03-01' GROUP BY 1"  # fmt: skip
    with pytest.raises(StatsError, match="at least"):
        stats(database, "forecast", sql=short)


def test_a_query_error_is_a_stats_error(database: Path) -> None:
    with pytest.raises(StatsError, match="nope"):
        stats(database, "trend", sql="SELECT nope FROM sales")


LINEAR = (
    "SELECT DATE '2025-01-01' + INTERVAL (i) MONTH AS month, 100 + 10 * i AS total "
    "FROM range(6) r(i)"
)
LONG_MONTHLY = (
    "SELECT DATE '2022-01-01' + INTERVAL (i) MONTH AS month, "
    "1000 + 10 * i + CASE WHEN i % 12 = 11 THEN 300 ELSE 0 END AS total FROM range(48) r(i)"
)


def test_notes_are_codes_with_params_and_text(database: Path) -> None:
    result = run_stats(database, "trend", MONTHLY, "month", "revenue",
                       data_start=dt.date(2025, 1, 15), data_end=DATA[1])  # fmt: skip

    first = result["notes"][0]
    assert first["code"] == "unfinished_first"
    assert first["params"] == {"period": "2025-01", "data_start": "2025-01-15"}
    assert "unfinished" in first["text"]


def test_trend_table_has_the_fitted_line(database: Path) -> None:
    result = run_stats(database, "trend", LINEAR, "month", "total", data_start=None, data_end=None)

    assert result["table"]["columns"] == ["month", "total", "fitted"]
    # y = 100 + 10t exactly, so the line goes through every point.
    assert [row[2] for row in result["table"]["rows"]] == pytest.approx(
        [100, 110, 120, 130, 140, 150]
    )


def test_forecast_table_keeps_history_then_horizon(database: Path) -> None:
    result = run_stats(database, "forecast", LONG_MONTHLY, "month", "total",
                       data_start=None, data_end=None, periods=3)  # fmt: skip

    table = result["table"]
    rows = table["rows"]
    assert table["columns"] == ["month", "total", "forecast", "low_95", "high_95"]
    assert len(rows) == 36 + 3  # 48 months of history, the last 36 kept
    assert rows[0][0] == "2023-01-01"
    assert rows[35][1] is not None and rows[35][2] is None
    assert rows[36][1] is None and rows[36][2] is not None
    assert rows[36][3] < rows[36][2] < rows[36][4]
    assert not table["truncated"]


def test_an_engine_error_is_kind_engine(database: Path) -> None:
    with pytest.raises(StatsError) as error:
        run_stats(database, "trend", "SELECT nope FROM missing", "a", "b",
                  data_start=None, data_end=None)  # fmt: skip
    assert error.value.kind == "engine"


def test_an_analysis_problem_is_kind_analysis(database: Path) -> None:
    with pytest.raises(StatsError) as error:
        stats(database, "trend", y="sales")
    assert error.value.kind == "analysis"


def test_a_correlation_note_is_a_code(tmp_path: Path) -> None:
    path = tmp_path / "gaps.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE t AS SELECT i AS a, CASE WHEN i = 3 THEN NULL ELSE i * 2 END AS b "
            "FROM range(10) r(i)"
        )
    result = run_stats(path, "correlation", "SELECT a, b FROM t", "a", "b",
                       data_start=None, data_end=None)  # fmt: skip

    assert result["notes"] == [
        {"code": "rows_without_value", "params": {"count": 1}, "text": "1 rows lack a value"}
    ]


def test_a_trend_leaves_out_rows_without_a_period(tmp_path: Path) -> None:
    path = tmp_path / "gaps.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE t AS SELECT CASE WHEN i = 6 THEN NULL ELSE DATE '2025-01-01' + "
            "INTERVAL (i) MONTH END AS m, 100 + 10 * i AS v FROM range(7) r(i)"
        )
    result = run_stats(
        path, "trend", "SELECT m, v FROM t", "m", "v", data_start=None, data_end=None
    )

    assert result["summary"]["periods"] == 6
    assert result["notes"][0]["code"] == "missing_dates"
