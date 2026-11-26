"""Fixed analyses on a query result: explain_change, trend, forecast, correlation, outliers.

See docs/adr/0051. The model names a query and its columns; this code picks the method, so a small
model cannot choose a naive one or copy numbers in by hand. Runs inside the sandbox on the clean
dataset, after the SQL guard in the Worker, over every row the query returns rather than the few
the model reads. explain_change lives in da_agent.storage.change and uses the helpers here.
"""

import datetime as dt
import itertools
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import duckdb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from da_agent.storage.duckdb_query import ENGINE_CONFIG, MAX_ERROR_CHARS

KINDS = ("explain_change", "trend", "forecast", "correlation", "outliers")
MAX_INPUT_ROWS = 1_000_000
MAX_TABLE_ROWS = 50
MAX_HORIZON = 12
MAX_DIMENSIONS = 4
# A forecast's table shows this much history before the forecast rows, for its chart.
HISTORY_ROWS = 36
SIGNIFICANCE = 0.05
# Iglewicz and Hoaglin: |modified z| above 3.5 is an outlier. 0.6745 makes MAD comparable to sd.
OUTLIER_Z = 3.5
GRAINS = ("day", "week", "month", "quarter", "year")
REGULAR = (
    "x must hold one date per regular period (day, week, month, quarter, or year); group the "
    "query by date_trunc('month', <date column>) or a similar period"
)

StatsErrorKind = Literal["analysis", "engine"]
# One period of a series: its first day and its value.
Point = tuple[dt.date, float]


class StatsError(ValueError):
    """The analysis cannot run. The message tells the model what to change. kind is "engine" when
    the query itself failed, and "analysis" when the result does not fit the analysis."""

    def __init__(self, message: str, kind: StatsErrorKind = "analysis") -> None:
        super().__init__(message)
        self.kind: StatsErrorKind = kind


class Note(TypedDict):
    """A choice code made: a fixed code and its values for the web, and a sentence for the model."""

    code: str
    params: dict[str, Any]
    text: str


def note(code: str, text: str, **params: Any) -> Note:
    return {"code": code, "params": params, "text": text}


def run_stats(
    database: Path,
    kind: str,
    sql: str,
    x: str,
    y: str,
    *,
    data_start: dt.date | None,
    data_end: dt.date | None,
    periods: int = 1,
    dimensions: Sequence[str] = (),
    before: tuple[dt.date, dt.date] | None = None,
    after: tuple[dt.date, dt.date] | None = None,
) -> dict[str, Any]:
    """Return {"kind", "summary", "table", "notes"}, or raise StatsError."""
    if kind not in KINDS:
        raise StatsError(f"kind must be one of {list(KINDS)}")
    if kind == "explain_change":
        if before is None or after is None:
            raise StatsError("explain_change needs before and after date ranges")
        if not 1 <= len(dimensions) <= MAX_DIMENSIONS:
            raise StatsError(
                f"explain_change needs 1 to {MAX_DIMENSIONS} dimensions to split the change by"
            )
    frame = _query(database, sql)
    for name in (x, y):
        if name not in frame.columns:
            raise StatsError(f"no column {name!r}; the query returns {list(frame.columns)}")
    if kind == "explain_change" and before is not None and after is not None:
        from da_agent.storage.change import explain_change

        return explain_change(
            frame, x, y, dimensions, before, after, data_start=data_start, data_end=data_end
        )
    if kind == "correlation":
        return _correlation(frame, x, y)
    if kind == "outliers":
        return _outliers(frame, x, y)
    series, grain, notes = _series(frame, x, y, data_start, data_end)
    if kind == "trend":
        return _trend(series, grain, x, y, notes)
    return _forecast(series, grain, x, y, notes, periods)


def _query(database: Path, sql: str) -> pd.DataFrame:
    try:
        with duckdb.connect(str(database), config=ENGINE_CONFIG) as connection:
            cursor = connection.execute(sql)
            frame = cursor.fetch_df_chunk(MAX_INPUT_ROWS // 2048 + 1)
            if cursor.fetchone() is not None:
                raise StatsError(
                    f"the query returns more than {MAX_INPUT_ROWS} rows; aggregate it first"
                )
    except duckdb.Error as error:
        raise StatsError(str(error).strip()[:MAX_ERROR_CHARS], "engine") from error
    return frame


def numbers(frame: pd.DataFrame, name: str) -> NDArray[np.float64]:
    """A column as floats, NaN where a value is missing or not a number."""
    values = pd.Series(pd.to_numeric(pd.Series(frame[name]), errors="coerce"))
    array = values.to_numpy(dtype=np.float64, na_value=np.nan)
    if np.isnan(array).all():
        raise StatsError(f"column {name!r} holds no numbers")
    return array


# Periods


def _month_index(day: dt.date) -> int:
    return day.year * 12 + day.month - 1


def _start_of(index: int) -> dt.date:
    return dt.date(index // 12, index % 12 + 1, 1)


def grain_of(days: list[dt.date]) -> str:
    """The period one row stands for, read from sorted distinct period starts."""
    if all(d.day == 1 for d in days):
        steps = {b - a for a, b in itertools.pairwise(map(_month_index, days))}
        if all(s % 12 == 0 for s in steps) and len({d.month for d in days}) == 1:
            return "year"
        if all(s % 3 == 0 for s in steps) and all(d.month in (1, 4, 7, 10) for d in days):
            return "quarter"
        return "month"
    gaps = {(b - a).days for a, b in itertools.pairwise(days)}
    if len({d.weekday() for d in days}) == 1 and all(g % 7 == 0 for g in gaps):
        return "week"
    return "day"


def step(day: dt.date, grain: str, count: int = 1) -> dt.date:
    """The start of the period count periods after the one starting on day."""
    if grain == "day":
        return day + dt.timedelta(days=count)
    if grain == "week":
        return day + dt.timedelta(weeks=count)
    months = {"month": 1, "quarter": 3, "year": 12}[grain] * count
    return _start_of(_month_index(day) + months)


def _position(day: dt.date, first: dt.date, grain: str) -> int:
    if grain == "day":
        return (day - first).days
    if grain == "week":
        return (day - first).days // 7
    size = {"month": 1, "quarter": 3, "year": 12}[grain]
    return (_month_index(day) - _month_index(first)) // size


def label(day: dt.date, grain: str) -> str:
    return day.strftime("%Y-%m") if grain in ("month", "quarter", "year") else day.isoformat()


def days_of(frame: pd.DataFrame, x: str) -> list[dt.date]:
    """x as whole dates, or StatsError."""
    column = pd.Series(frame[x])
    # Numbers are not dates; parsing them would read them as epochs or as text.
    if pd.api.types.is_numeric_dtype(column) or pd.api.types.is_bool_dtype(column):
        raise StatsError(REGULAR)
    try:
        moments = pd.Series(pd.to_datetime(column, format="ISO8601"))
    except (ValueError, TypeError) as error:
        raise StatsError(REGULAR) from error
    if moments.isna().any() or (moments.dt.normalize() != moments).any():
        raise StatsError(REGULAR)
    return [moment.date() for moment in moments]


def without_missing_dates(frame: pd.DataFrame, x: str) -> tuple[pd.DataFrame, list[Note]]:
    """The rows that have an x, and a note when some had none: uploads often hold blank dates,
    and one of them should not refuse the whole analysis."""
    present = pd.Series(frame[x]).notna()
    missing = int((~present).sum())
    if not missing:
        return frame, []
    text = f"{missing} rows have no {x}; left out"
    return frame.loc[present].reset_index(drop=True), [
        note("missing_dates", text, count=missing, column=x)
    ]


def _series(
    frame: pd.DataFrame, x: str, y: str, data_start: dt.date | None, data_end: dt.date | None
) -> tuple[list[Point], str, list[Note]]:
    """(period start, y) sorted by period, with unfinished first and last periods left out."""
    frame, dropped = without_missing_dates(frame, x)
    days = days_of(frame, x)
    if len(set(days)) < len(days):
        raise StatsError(f"x repeats a period; {REGULAR}")
    series: list[Point] = sorted(zip(days, numbers(frame, y).tolist(), strict=True))
    if len(series) < 2:
        raise StatsError("need at least 2 periods")
    grain = grain_of([day for day, _ in series])
    notes: list[Note] = list(dropped)
    first, last = series[0][0], series[-1][0]
    if data_start is not None and data_start > first:
        period = label(first, grain)
        notes.append(
            note(
                "unfinished_first",
                f"{period} is unfinished: the data starts on {data_start}; left out",
                period=period,
                data_start=data_start.isoformat(),
            )
        )
        series = series[1:]
    if data_end is not None and data_end < step(last, grain) - dt.timedelta(days=1):
        period = label(last, grain)
        notes.append(
            note(
                "unfinished_last",
                f"{period} is unfinished: the data ends on {data_end}; left out",
                period=period,
                data_end=data_end.isoformat(),
            )
        )
        series = series[:-1]
    if len(series) >= 2:
        expected = _position(series[-1][0], series[0][0], grain) + 1
        if expected > len(series):
            count = expected - len(series)
            text = f"{count} {grain}s between have no rows"
            notes.append(note("missing_periods", text, count=count, grain=grain))
    count = sum(1 for _, value in series if math.isnan(value))
    if count:
        text = f"{count} periods have no {y} value; left out"
        notes.append(note("missing_values", text, count=count, column=y))
        series = [point for point in series if not math.isnan(point[1])]
    return series, grain, notes


# Analyses


def rounded(value: float | None, digits: int = 2) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "columns": columns,
        "rows": rows[:MAX_TABLE_ROWS],
        "truncated": len(rows) > MAX_TABLE_ROWS,
    }


def _trend(series: list[Point], grain: str, x: str, y: str, notes: list[Note]) -> dict[str, Any]:
    import statsmodels.api as sm

    if len(series) < 3:
        raise StatsError(f"a trend needs at least 3 whole {grain}s; it has {len(series)}")
    first, last = series[0][0], series[-1][0]
    t = np.array([_position(day, first, grain) for day, _ in series], dtype=float)
    values = np.array([value for _, value in series], dtype=float)
    fit: Any = sm.OLS(values, sm.add_constant(t)).fit()
    slope, p_value = float(fit.params[1]), float(fit.pvalues[1])
    mean = float(values.mean())
    if p_value < SIGNIFICANCE:
        direction = "increasing" if slope > 0 else "decreasing"
    else:
        direction = "no clear trend"
    summary = {
        "method": f"least-squares line through each whole {grain}",
        "grain": grain,
        "periods": len(series),
        "first_period": label(first, grain),
        "last_period": label(last, grain),
        "slope_per_period": rounded(slope),
        "slope_percent_of_mean": rounded(100 * slope / mean) if mean else None,
        "p_value": rounded(p_value, 4),
        "r_squared": rounded(float(fit.rsquared), 3),
        "direction": direction,
        "mean": rounded(mean),
    }
    fitted = [float(value) for value in np.asarray(fit.fittedvalues, dtype=float)]
    rows = [
        [day.isoformat(), rounded(value), rounded(line)]
        for (day, value), line in zip(series, fitted, strict=True)
    ]
    return {
        "kind": "trend",
        "summary": summary,
        "table": table([x, y, "fitted"], rows),
        "notes": notes,
    }


def _forecast(
    series: list[Point], grain: str, x: str, y: str, notes: list[Note], periods: int
) -> dict[str, Any]:
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    if not 1 <= periods <= MAX_HORIZON:
        raise StatsError(f"periods must be 1 to {MAX_HORIZON}")
    if len(series) < 4:
        raise StatsError(f"a forecast needs at least 4 whole {grain}s; it has {len(series)}")
    first, last = series[0][0], series[-1][0]
    count = _position(last, first, grain) + 1
    by_day = dict(series)
    full = np.array([by_day.get(step(first, grain, i), np.nan) for i in range(count)])
    if np.isnan(full).any():
        full = pd.Series(full).interpolate().to_numpy(dtype=np.float64)
        notes.append(
            note("interpolated", "missing periods were filled by straight lines between neighbours")
        )
    season = {"month": 12, "quarter": 4}.get(grain)
    seasonal = season is not None and len(full) >= 2 * season
    # The model reads the index of a Series, so the forecast rows can be read back by position.
    model = ETSModel(
        pd.Series(full),
        error="add",
        trend="add",
        damped_trend=len(full) >= 8,
        seasonal="add" if seasonal else None,
        seasonal_periods=season if seasonal else None,
    )
    fit = cast(Any, model.fit(disp=False))
    frame = fit.get_prediction(start=len(full), end=len(full) + periods - 1).summary_frame(
        alpha=0.05
    )
    history = [
        [day.isoformat(), rounded(value), None, None, None] for day, value in series[-HISTORY_ROWS:]
    ]
    ahead = [
        [
            step(last, grain, i + 1).isoformat(),
            None,
            rounded(float(mean)),
            rounded(float(low)),
            rounded(float(high)),
        ]
        for i, (mean, low, high) in enumerate(
            zip(frame["mean"], frame["pi_lower"], frame["pi_upper"], strict=True)
        )
    ]
    trend = "damped additive trend" if len(full) >= 8 else "additive trend"
    method = f"exponential smoothing (ETS) with {trend}" + (
        f" and {season}-{grain} seasonality" if seasonal else ""
    )
    summary = {"method": method, "grain": grain, "periods_used": len(full), "horizon": periods}
    columns = [x, y, "forecast", "low_95", "high_95"]
    return {
        "kind": "forecast",
        "summary": summary,
        "table": table(columns, history + ahead),
        "notes": notes,
    }


def _strength(r: float) -> str:
    size = abs(r)
    if size < 0.1:
        return "none"
    if size < 0.3:
        return "weak"
    if size < 0.5:
        return "moderate"
    return "strong"


def _correlation(frame: pd.DataFrame, x: str, y: str) -> dict[str, Any]:
    from scipy import stats

    xs, ys = numbers(frame, x), numbers(frame, y)
    both = ~np.isnan(xs) & ~np.isnan(ys)
    xs, ys = xs[both], ys[both]
    if len(xs) < 3:
        raise StatsError("a correlation needs at least 3 rows with both values")
    if len(np.unique(xs)) < 2 or len(np.unique(ys)) < 2:
        raise StatsError("a column is constant, so it correlates with nothing")
    pearson: Any = stats.pearsonr(xs, ys)
    spearman: Any = stats.spearmanr(xs, ys)
    r = float(pearson.statistic)
    summary = {
        "method": "Pearson (linear) and Spearman (rank) correlation over every row",
        "rows": len(xs),
        "pearson": rounded(r, 4),
        "spearman": rounded(float(spearman.statistic), 4),
        "p_value": rounded(float(pearson.pvalue), 4),
        "strength": _strength(r),
    }
    lacking = len(frame) - len(xs)
    notes = [note("rows_without_value", f"{lacking} rows lack a value", count=lacking)]
    return {
        "kind": "correlation",
        "summary": summary,
        "table": None,
        "notes": notes if lacking else [],
    }


def cell(value: Any) -> Any:
    """A plain JSON value for one result cell."""
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, dt.datetime) and value.time() == dt.time(0):
        value = value.date()
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return rounded(value)
    return value if isinstance(value, (int, str, bool)) or value is None else str(value)


def _outliers(frame: pd.DataFrame, x: str, y: str) -> dict[str, Any]:
    values = numbers(frame, y)
    kept = ~np.isnan(values)
    if kept.sum() < 5:
        raise StatsError("outliers need at least 5 values")
    median = float(np.median(values[kept]))
    mad = float(np.median(np.abs(values[kept] - median)))
    if mad == 0:
        raise StatsError(f"more than half of {y} is the same value, so nothing stands out")
    scores = 0.6745 * (values - median) / mad
    found = [int(i) for i in np.flatnonzero(kept & (np.abs(scores) > OUTLIER_Z))]
    order = sorted(found, key=lambda i: -abs(float(scores[i])))
    labels = list(pd.Series(frame[x]))
    rows = [
        [
            cell(labels[i]),
            cell(float(values[i])),
            rounded(float(scores[i])),
            "high" if scores[i] > 0 else "low",
        ]
        for i in order
    ]
    summary = {
        "method": f"robust z-score from median and MAD; |z| above {OUTLIER_Z} stands out",
        "rows": int(kept.sum()),
        "outliers": len(rows),
        "median": rounded(median),
        "threshold": OUTLIER_Z,
    }
    columns = [x, y, "robust_z", "side"]
    return {"kind": "outliers", "summary": summary, "table": table(columns, rows), "notes": []}
