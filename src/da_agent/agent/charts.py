"""Vega-Lite specs built by code from a query result.

The model names a mark and the result columns to plot; it never writes Vega-Lite. So a spec holds
only inline rows that a query returned, and none of the parts of Vega-Lite that fetch data or
evaluate expressions (data URLs, transforms, signals). See docs/adr/0015.
"""

import datetime as dt
import math
import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

from da_agent.agent.answer import DATE_PATTERN, QueryRecord
from da_agent.llm.types import ToolSpec
from da_agent.storage.sqlite_dataset import QueryResult

MAKE_CHART = "make_chart"
VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json"
MARKS = ("bar", "line", "point", "area", "arc")
# Bounds state and the final result, which carry every spec of the run.
MAX_CHARTS = 3
MAX_TITLE_CHARS = 120
# A timestamp at midnight, as DATE_TRUNC returns and the sandbox sends it: a date in disguise.
MIDNIGHT = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})[ T]00:00:00(\.0+)?")

MAKE_CHART_SPEC = ToolSpec(
    name=MAKE_CHART,
    description=(
        "Draw a chart from one read-only SELECT. Write the query so each row is one point, bar, "
        "or slice, then name the result columns to plot. Use it when the user asks for a chart, "
        "or when a trend or comparison reads better as one. You get back a confirmation, not the "
        "rows: numbers in your answer still need run_sql. 'arc' is a pie chart: x labels the "
        "slices and y sizes them."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "One SELECT that returns the plotted rows."},
            "mark": {"type": "string", "enum": list(MARKS)},
            "x": {"type": "string", "description": "Result column for the x axis or slice label."},
            "y": {"type": "string", "description": "Numeric result column for the y axis."},
            "color": {
                "type": "string",
                "description": "Optional result column that splits the data into series.",
            },
            "title": {"type": "string", "description": "A short title."},
        },
        "required": ["sql", "mark", "x", "y"],
    },
)


class ChartError(ValueError):
    """The chart cannot be built. The message tells the model how to fix it."""


def _plain(value: Any) -> Any:
    """A database value as JSON: Decimal to float, dates to ISO text, anything odd to text."""
    if isinstance(value, str):
        return _plain_text(value)
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, dt.datetime):
        return _plain(value.isoformat(sep=" "))
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _plain_text(value: str) -> str:
    midnight = MIDNIGHT.fullmatch(value)
    return midnight.group(1) if midnight else value


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _field_type(values: list[Any]) -> str:
    present = [v for v in values if v is not None]
    if present and all(_is_number(v) for v in present):
        return "quantitative"
    if present and all(isinstance(v, str) and DATE_PATTERN.match(v) for v in present):
        return "temporal"
    return "nominal"


def _time_unit(values: list[Any]) -> str | None:
    """Months or days as the unit, so axis ticks fall on them rather than between.

    Dates that all fall on the first of a month, as DATE_TRUNC('month', ...) returns, are months.
    """
    texts = [v for v in values if isinstance(v, str)]
    lengths = {len(v) for v in texts}
    if len(lengths) != 1:
        return None
    length = lengths.pop()
    if length == 10 and len(set(texts)) > 1 and all(v.endswith("-01") for v in texts):
        return "yearmonth"
    return {7: "yearmonth", 10: "yearmonthdate"}.get(length)


def _field(name: str) -> str:
    # Vega-Lite reads "." and "[]" in a field as nested access, so a flat column escapes them.
    for char in ("\\", ".", "[", "]"):
        name = name.replace(char, "\\" + char)
    return name


def chart_spec(arguments: dict[str, Any], result: QueryResult) -> dict[str, Any]:
    """Build a Vega-Lite spec from make_chart arguments and the result of their query."""
    mark = arguments.get("mark")
    if mark not in MARKS:
        raise ChartError(f"mark must be one of {list(MARKS)}")
    names: dict[str, str] = {}
    for role in ("x", "y", "color"):
        name = arguments.get(role)
        if name is None and role == "color":
            continue
        if not isinstance(name, str) or not name:
            raise ChartError(f"{role} is required: a column name from the query result")
        if name not in result.columns:
            raise ChartError(
                f"{role} names {name!r}, which the query does not return; "
                f"its columns are {result.columns}"
            )
        names[role] = name
    if not result.rows:
        raise ChartError("the query returned no rows, so there is nothing to draw")
    if result.truncated:
        raise ChartError(
            f"the query returned more than {len(result.rows)} rows; aggregate or add LIMIT so "
            "each row is one point of the chart"
        )

    values = [
        {column: _plain(cell) for column, cell in zip(result.columns, row, strict=True)}
        for row in result.rows
    ]
    types = {role: _field_type([v[name] for v in values]) for role, name in names.items()}
    if types["y"] != "quantitative":
        raise ChartError(
            f"y names {names['y']!r}, which is not numeric; y must be a numeric column"
        )

    def encode(role: str) -> dict[str, Any]:
        encoded: dict[str, Any] = {"field": _field(names[role]), "type": types[role]}
        if types[role] == "temporal":
            unit = _time_unit([v[names[role]] for v in values])
            if unit:
                # The column name, not Vega-Lite's "month (year-month)".
                encoded["timeUnit"], encoded["title"] = unit, names[role]
                if role == "x" and mark == "bar" and unit == "yearmonth":
                    # One bar per month, under its label: on a time scale a bar runs from its
                    # month's tick to the next, which few bars make plain. Vega-Lite allows a
                    # band scale only for discrete types, so the field becomes ordinal.
                    encoded["type"] = "ordinal"
        elif types[role] == "nominal" and role == "x" and mark != "arc":
            # Keep the order of the query rows: Vega-Lite would sort labels alphabetically.
            encoded["sort"] = None
        return encoded

    if mark == "arc":
        encoding = {"theta": encode("y"), "color": encode("x")}
    else:
        encoding = {"x": encode("x"), "y": encode("y")}
        if "color" in names:
            encoding["color"] = encode("color")
    title = arguments.get("title")
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": title[:MAX_TITLE_CHARS] if isinstance(title, str) else "",
        "data": {"values": values},
        "mark": {"type": mark, "tooltip": True},
        "encoding": encoding,
    }


# Charts of fixed analyses (ADR 0051). Code draws them from the analysis table, so they hold only
# numbers the analysis returned, and no transform: a waterfall's bar ends are computed here.

WATERFALL_COLORS = {"total": "#64748b", "up": "#16a34a", "down": "#dc2626"}


def analysis_chart(record: QueryRecord) -> dict[str, Any] | None:
    """A Vega-Lite spec for an analysis record, or None for kinds drawn as tables."""
    analysis = record.analysis or {}
    kind = analysis.get("kind")
    if kind == "explain_change":
        return _waterfall(record, analysis)
    if kind not in ("trend", "forecast") or not record.rows:
        return None
    values = [
        {column: _plain(cell) for column, cell in zip(record.columns, row, strict=True)}
        for row in record.rows
    ]
    x, y = record.columns[0], record.columns[1]
    unit = _time_unit([v[x] for v in values])
    x_encoding: dict[str, Any] = {"field": _field(x), "type": "temporal", "title": x}
    if unit:
        x_encoding["timeUnit"] = unit

    def line(field: str, dashed: bool) -> dict[str, Any]:
        mark: dict[str, Any] = {"type": "line", "point": not dashed, "tooltip": True}
        if dashed:
            mark["strokeDash"] = [6, 4]
        y_encoding = {"field": _field(field), "type": "quantitative", "title": y}
        return {"mark": mark, "encoding": {"x": x_encoding, "y": y_encoding}}

    if kind == "trend":
        layers = [line(y, False), line("fitted", True)]
    else:
        band = {
            "mark": {"type": "area", "opacity": 0.2, "tooltip": True},
            "encoding": {
                "x": x_encoding,
                "y": {"field": "low_95", "type": "quantitative", "title": y},
                "y2": {"field": "high_95"},
            },
        }
        layers = [band, line(y, False), line("forecast", True)]
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": y[:MAX_TITLE_CHARS],
        "data": {"values": values},
        "layer": layers,
    }


def _span_label(span: Any) -> str:
    """A range as people say it: a whole month as 2026-10, one day as that day."""
    if not isinstance(span, dict):
        return ""
    start, end = str(span.get("start", "")), str(span.get("end", ""))
    if start == end:
        return start
    try:
        first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        return f"{start} to {end}"
    following = last + dt.timedelta(days=1)
    whole_month = first.day == 1 and (first.year, first.month) == (last.year, last.month)
    if whole_month and following.day == 1:
        return start[:7]
    return f"{start} to {end}"


def _waterfall(record: QueryRecord, analysis: dict[str, Any]) -> dict[str, Any] | None:
    summary = analysis.get("summary") or {}
    ranked = summary.get("dimensions") or []
    before, after = summary.get("before") or {}, summary.get("after") or {}
    if not ranked or "total" not in before or "total" not in after:
        return None
    dimension = ranked[0]
    first, last = float(before["total"]), float(after["total"])
    steps: list[dict[str, Any]] = [
        {"step": _span_label(before), "start": first, "end": first, "kind": "total", "total": first}
    ]
    level = first
    for row in record.rows:
        if row[0] != dimension:
            continue
        moved = float(row[4] or 0)
        steps.append(
            {
                "step": str(row[1]),
                "start": level,
                "end": round(level + moved, 2),
                "kind": "up" if moved >= 0 else "down",
                "total": None,
            }
        )
        level = round(level + moved, 2)
    steps.append(
        {"step": _span_label(after), "start": last, "end": last, "kind": "total", "total": last}
    )
    # Changes are small next to the totals, so the axis zooms to the levels. A total is then a
    # tick with its value written, never a bar: a bar on a cut axis makes a drop look larger
    # than it is. A row without a total is left out of the tick and text layers.
    levels = [float(step["end"]) for step in steps] + [float(step["start"]) for step in steps]
    low, high = min(levels), max(levels)
    pad = (high - low) * 0.5 if high > low else abs(high) * 0.05 or 1.0
    floor = round(low - pad, 2)
    top = round(high + (high - floor) * 0.08, 2)
    y = str((analysis.get("arguments") or {}).get("y") or "")
    total = {"field": "total", "type": "quantitative"}
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": y[:MAX_TITLE_CHARS],
        "data": {"values": steps},
        "encoding": {
            "x": {
                "field": "step",
                "type": "ordinal",
                "sort": None,
                "title": dimension,
                # Tilted, so segment names stay apart on a phone as well as a wide screen.
                "axis": {"labelAngle": -40},
            }
        },
        "layer": [
            {
                "mark": {"type": "bar", "tooltip": True},
                "encoding": {
                    "y": {
                        "field": "start",
                        "type": "quantitative",
                        "title": y,
                        "scale": {"zero": False, "nice": False, "domain": [floor, top]},
                    },
                    "y2": {"field": "end"},
                    "color": {
                        "field": "kind",
                        "type": "nominal",
                        "legend": None,
                        "scale": {
                            "domain": list(WATERFALL_COLORS),
                            "range": list(WATERFALL_COLORS.values()),
                        },
                    },
                },
            },
            {
                "mark": {"type": "tick", "thickness": 3, "color": WATERFALL_COLORS["total"]},
                "encoding": {"y": total},
            },
            {
                "mark": {"type": "text", "dy": -10},
                "encoding": {"y": total, "text": {**total, "format": ",.0f"}},
            },
        ],
    }


def with_analysis_charts(records: list[QueryRecord]) -> list[QueryRecord]:
    """The records, with each analysis's chart in its analysis."""
    attached = []
    for record in records:
        chart = analysis_chart(record) if record.analysis is not None else None
        if chart is not None and record.analysis is not None:
            record = replace(record, analysis={**record.analysis, "chart": chart})
        attached.append(record)
    return attached
