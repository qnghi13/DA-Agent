"""A standalone HTML report of one agent run, where every number links to the query cell behind it.

The report reads only the run's result: the answer, the queries the model ran with the rows it
saw, and the chart specs. It runs nothing. Every piece of text in it can come from uploaded data or
the model, so all of it is escaped, chart specs are embedded as inert JSON, and a Content Security
Policy allows only the pinned chart libraries and the one inline script that draws the charts.
See docs/adr/0016.
"""

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass
from typing import Any

from da_agent.agent.answer import AgentResult, QueryRecord
from da_agent.agent.verify import traces

# Vega 5 builds, pinned by version and hash. The interpreter lets Vega run without eval, so the
# policy below needs no 'unsafe-eval'.
CHART_LIBRARIES = (
    (
        "vega@5.33.0/build/vega.min.js",
        "sha384-Kpi0LDGE2Pg6N3+B3LiVIugYJ3rGL3AAqlnkRLfFxZtmejptl+TJ3iKY54HF5U3l",
    ),
    (
        "vega-lite@5.23.0/build/vega-lite.min.js",
        "sha384-D9LYH0esGjcxQJsBuxOuXtCDJGXRWW1+KhluzWPqi0rLJmiR/ygPChefaD+rFFDQ",
    ),
    (
        # 1.2.x fails inside Vega 5.33 and 2.x ships only as an ES module; 1.0.5 works.
        "vega-interpreter@1.0.5/build/vega-interpreter.min.js",
        "sha384-9LanOUB60Mnbvh+fwRI/G5STHUPkO7uZitLePc1jEQdjQWSQy34Oc+TzpdvXTIh2",
    ),
    (
        "vega-embed@6.29.0/build/vega-embed.min.js",
        "sha384-M+Ax7e/WFJpxSOF09HzI+Sj4wg9ottVd/uxmV2ItGGh02fLH28t2FAOJx3TJBap5",
    ),
)
CDN = "https://cdn.jsdelivr.net/npm/"

DRAW_CHARTS = """
for (const holder of document.querySelectorAll("[data-spec]")) {
  if (typeof vegaEmbed !== "function") break;
  const spec = JSON.parse(document.getElementById(holder.dataset.spec).textContent);
  spec.width = "container";
  const options = {actions: false, renderer: "svg", ast: true, expr: vega.expressionInterpreter};
  if (matchMedia("(prefers-color-scheme: dark)").matches) options.theme = "dark";
  vegaEmbed(holder, spec, options).then(() => holder.classList.add("drawn"), () => {});
}
"""

STYLE = """
:root { --bg: #fbfbf9; --fg: #1d1d1b; --muted: #5f5f5a; --line: #deded8; --link: #1f5fbf;
  --hit: #fff1b8; --warn: #a3401f; --code: #f1f1ec; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #ececea; --muted: #a3a39d; --line: #34342f; --link: #8ab4f8;
    --hit: #5a4a10; --warn: #f29b7a; --code: #222220; }
}
body { background: var(--bg); color: var(--fg); margin: 0;
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.5rem; line-height: 1.3; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 36px 0 12px; border-bottom: 1px solid var(--line);
  padding-bottom: 6px; }
h3 { font-size: 0.95rem; margin: 20px 0 8px; }
.meta, .note { color: var(--muted); font-size: 0.9rem; }
.answer { font-size: 1.6rem; font-weight: 600; margin: 8px 0; }
.status { color: var(--warn); font-weight: 600; }
a.num { color: var(--link); text-decoration: underline; text-underline-offset: 3px; }
.untraced { text-decoration: underline dotted var(--warn); text-underline-offset: 3px; }
pre { background: var(--code); padding: 10px 12px; border-radius: 6px; overflow-x: auto;
  font-size: 0.85rem; white-space: pre-wrap; word-break: break-word; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 0.9rem; margin: 8px 0; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; }
td.n { text-align: right; font-variant-numeric: tabular-nums; }
td:target { background: var(--hit); outline: 2px solid var(--link); }
figure { margin: 16px 0; }
.chart { min-height: 40px; width: 100%; }
.chart.drawn .fallback { display: none; }
"""

DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
NUMBER = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)*(?![\w])(%?)")


@dataclass(frozen=True)
class _Cell:
    query: int
    row: int
    column: int
    value: float

    @property
    def anchor(self) -> str:
        return f"q{self.query}-r{self.row}-c{self.column}"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _cells(queries: list[QueryRecord]) -> list[_Cell]:
    """Every numeric cell, latest query first, so a number links to the latest query holding it."""
    cells = []
    for index in range(len(queries), 0, -1):
        for row, values in enumerate(queries[index - 1].rows, start=1):
            for column, value in enumerate(values, start=1):
                if _is_number(value):
                    cells.append(_Cell(index, row, column, float(value)))
    return cells


def _source(value: float, cells: list[_Cell], percent: bool) -> _Cell | None:
    return next((c for c in cells if traces(value, [c.value], percent)), None)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


class _Numbers:
    """Renders numbers as links to their source cell and counts those it cannot trace."""

    def __init__(self, cells: list[_Cell]) -> None:
        self.cells = cells
        self.traced = 0
        self.untraced = 0

    def link(self, shown: str, candidates: list[float], percent: bool, count: bool) -> str:
        cell = next((c for value in candidates if (c := _source(value, self.cells, percent))), None)
        if count:
            self.traced += cell is not None
            self.untraced += cell is None
        if cell is None:
            return (
                f'<span class="untraced" title="Not found in any query result">{_e(shown)}</span>'
            )
        title = f"Query {cell.query}, row {cell.row}, column {cell.column}"
        return f'<a class="num" href="#{cell.anchor}" title="{title}">{_e(shown)}</a>'

    def value(self, value: Any, percent: bool) -> str:
        if _is_number(value):
            return self.link(_show(value), [float(value)], percent, count=True)
        return _e(value)

    def prose(self, text: str) -> str:
        """Text with each number linked. Dates are left as text. Both 1,234.5 and 1.234,5 are
        read, since the agent answers in the asker's language."""
        parts, last = [], 0
        for date_or_number in re.finditer(f"{DATE.pattern}|{NUMBER.pattern}", text):
            token = date_or_number.group(0)
            parts.append(_e(text[last : date_or_number.start()]))
            last = date_or_number.end()
            if DATE.fullmatch(token):
                parts.append(_e(token))
                continue
            digits = token.rstrip("%")
            percent = token.endswith("%")
            value = _read_number(digits)
            candidates = [] if value is None else [value]
            parts.append(self.link(digits, candidates, percent, count=False))
            parts.append("%" if percent else "")
        parts.append(_e(text[last:]))
        return "".join(parts)


def _read_number(text: str) -> float | None:
    """Read 1,234.5 or 1.234,5. With two kinds of separator the last one is the decimal point.
    One kind used more than once, or once before exactly three digits (1,000 or 1.000), groups
    thousands, unless the number starts with 0 (0.714). Otherwise it is the decimal point."""
    sign, body = ("-", text[1:]) if text.startswith("-") else ("", text)
    separators = [char for char in body if char in ",."]
    if not separators:
        return float(text)
    groups = re.split(r"[.,]", body)
    if len(set(separators)) == 2:
        last = body.rfind(separators[-1])
        whole, fraction = body[:last], body[last + 1 :]
        return float(sign + re.sub(r"[.,]", "", whole) + "." + fraction)
    if len(separators) > 1 or (len(groups[-1]) == 3 and groups[0] != "0"):
        return float(sign + "".join(groups))
    return float(sign + groups[0] + "." + groups[1])


def _show(value: Any) -> str:
    """A value as text. Numbers of five digits or more get thousands separators; shorter ones,
    such as a year, do not."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if _is_number(value) and abs(value) >= 10_000:
        return f"{value:,}"
    return str(value)


def _answer(result: AgentResult, numbers: _Numbers) -> str:
    answer = result.answer
    if answer is None:
        reason = f": {_e(result.error)}" if result.error else ""
        status = f"No answer ({_e(result.stop_reason)}){reason}"
        return f'<p class="status">{status}</p>'
    percent = answer.kind == "percent"
    if answer.kind == "unanswerable":
        body = '<p class="answer">The data cannot answer this question.</p>'
    elif answer.kind == "table" and isinstance(answer.value, dict):
        head = "".join(f"<th>{_e(c)}</th>" for c in answer.value["columns"])
        rows = "".join(
            "<tr>"
            + "".join(
                f'<td class="{"n" if _is_number(v) else ""}">{numbers.value(v, percent)}</td>'
                for v in row
            )
            + "</tr>"
            for row in answer.value["rows"]
        )
        body = f'<div class="scroll"><table><tr>{head}</tr>{rows}</table></div>'
    elif answer.kind == "list" and isinstance(answer.value, list):
        items = "".join(f"<li>{numbers.value(v, percent)}</li>" for v in answer.value)
        body = f"<ul>{items}</ul>"
    else:
        suffix = "%" if percent else ""
        body = f'<p class="answer">{numbers.value(answer.value, percent)}{suffix}</p>'
    if answer.reason:
        body += f"<p>{numbers.prose(answer.reason)}</p>"
    return body


def _summary(numbers: _Numbers) -> str:
    total = numbers.traced + numbers.untraced
    if not total:
        return ""
    if not numbers.untraced:
        text = f"All {total} numbers in the answer link to the query cell they came from."
        if total == 1:
            text = "The number in the answer links to the query cell it came from."
        return f'<p class="note">{text}</p>'
    return (
        f'<p class="status">{numbers.untraced} of {total} numbers in the answer are not in any '
        "query result.</p>"
    )


def _table(columns: list[str], rows: list[list[Any]], anchor: str | None) -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row, start=1):
            cell_id = f' id="{anchor}-r{r}-c{c}"' if anchor and _is_number(value) else ""
            css = ' class="n"' if _is_number(value) else ""
            cells.append(f"<td{cell_id}{css}>{'' if value is None else _e(_show(value))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="scroll"><table><tr>{head}</tr>{"".join(body)}</table></div>'


def _queries(queries: list[QueryRecord]) -> str:
    if not queries:
        return ""
    parts = ["<h2>Queries</h2>"]
    for index, query in enumerate(queries, start=1):
        note = " Rows were cut at the query limit." if query.truncated else ""
        parts.append(
            f'<section id="q{index}"><h3>Query {index}</h3><pre>{_e(query.sql)}</pre>'
            f"{_table(query.columns, query.rows, f'q{index}')}"
            + (f'<p class="note">{note.strip()}</p>' if note else "")
            + "</section>"
        )
    return "".join(parts)


def _json_for_script(value: Any) -> str:
    # Inside <script>, "</script>" or "<!--" in a string would end the block, so escape < > &.
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _charts(result: AgentResult) -> str:
    steps = [s for s in result.steps if s.chart is not None]
    if not steps:
        return ""
    parts = ["<h2>Charts</h2>"]
    for index, step in enumerate(steps, start=1):
        spec = step.chart or {}
        values = spec.get("data", {}).get("values", [])
        columns = list(values[0]) if values else []
        rows = [[row.get(c) for c in columns] for row in values]
        title = spec.get("title") or f"Chart {index}"
        parts.append(
            f'<figure><div class="chart" id="chart-{index}" data-spec="chart-{index}-spec">'
            f'<div class="fallback"><h3>{_e(title)}</h3><p class="note">The chart library did '
            "not load, so the chart is not drawn. Its data is below.</p></div></div>"
            f'<script type="application/json" id="chart-{index}-spec">'
            f"{_json_for_script(spec)}</script>"
            f"<details><summary>Data and query</summary><pre>{_e(step.sql or '')}</pre>"
            f"{_table(columns, rows, None)}</details></figure>"
        )
    return "".join(parts)


def _script_hash(script: str) -> str:
    digest = hashlib.sha256(script.encode()).digest()
    return "'sha256-" + base64.b64encode(digest).decode() + "'"


# Sent in the page and, by the API, as a header: only the pinned libraries and the inline script
# that draws charts may run, and nothing may be fetched or submitted.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; "
    f"form-action 'none'; script-src {CDN} {_script_hash(DRAW_CHARTS)}"
)


def render_report(result: AgentResult, *, question: str, as_of: str | None = None) -> str:
    """The report as one HTML document. Charts need the pinned libraries from the CDN; without
    them, the report still shows each chart's data and every number's source."""
    numbers = _Numbers(_cells(result.queries))
    answer = _answer(result, numbers)
    charts = _charts(result)
    scripts = ""
    if charts:
        scripts = "".join(
            f'<script src="{CDN}{path}" integrity="{digest}" crossorigin="anonymous"></script>'
            for path, digest in CHART_LIBRARIES
        )
        scripts += f"<script>{DRAW_CHARTS}</script>"
    as_of_text = f" · data as of {_e(as_of)}" if as_of else ""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}">'
        f"<title>Analysis report</title><style>{STYLE}</style></head><body><main>"
        f"<h1>{_e(question)}</h1>"
        f'<p class="meta">{len(result.queries)} queries · stop reason '
        f"{_e(result.stop_reason)}{as_of_text}</p>"
        f"<h2>Answer</h2>{answer}{_summary(numbers)}{charts}{_queries(result.queries)}"
        f"</main>{scripts}</body></html>\n"
    )
