"""Vega-Lite specs built by code from a query result.

The model names a mark and the result columns to plot; it never writes Vega-Lite. So a spec holds
only inline rows that a query returned, and none of the parts of Vega-Lite that fetch data or
evaluate expressions (data URLs, transforms, signals). See docs/adr/0015.
"""

import datetime as dt
import math
from decimal import Decimal
from typing import Any

from da_agent.agent.answer import DATE_PATTERN
from da_agent.llm.types import ToolSpec
from da_agent.storage.sqlite_dataset import QueryResult

MAKE_CHART = "make_chart"
VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
MARKS = ("bar", "line", "point", "area", "arc")
# Bounds state and the final result, which carry every spec of the run.
MAX_CHARTS = 3
MAX_TITLE_CHARS = 120

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
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return str(value)


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
    """Months or days as the unit, so axis ticks fall on them rather than between."""
    lengths = {len(v) for v in values if isinstance(v, str)}
    return {7: "yearmonth", 10: "yearmonthdate"}.get(lengths.pop()) if len(lengths) == 1 else None


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

    def encode(role: str) -> dict[str, str]:
        encoded = {"field": _field(names[role]), "type": types[role]}
        if types[role] == "temporal":
            unit = _time_unit([v[names[role]] for v in values])
            if unit:
                encoded["timeUnit"] = unit
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
