"""Standalone HTML reports: every number in the answer links to the query cell it came from."""

from html.parser import HTMLParser
from typing import Any

import pytest

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord, ToolStep
from da_agent.agent.charts import chart_spec
from da_agent.reports.html import render_report
from da_agent.storage.sqlite_dataset import QueryResult

TOTAL = QueryRecord(sql="SELECT SUM(total) AS t FROM orders", columns=["t"], rows=[[350]])
BY_CHANNEL = QueryRecord(
    sql="SELECT channel, SUM(total) AS t FROM orders GROUP BY 1",
    columns=["channel", "t"],
    rows=[["web", 250], ["shop", 100]],
)
SHARE = QueryRecord(sql="SELECT 250.0 / 350 AS share", columns=["share"], rows=[[0.7142857]])


class Page(HTMLParser):
    """The parts of a report the tests read: number links, element ids, scripts, and text."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.untraced: list[str] = []
        self.ids: set[str] = set()
        self.scripts: list[dict[str, str | None]] = []
        self.metas: list[dict[str, str | None]] = []
        self.text: list[str] = []
        self._open: tuple[str, str] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids.add(found["id"] or "")
        classes = (found.get("class") or "").split()
        if tag == "a" and "num" in classes:
            self._open = ("link", found.get("href") or "")
        elif tag == "span" and "untraced" in classes:
            self._open = ("untraced", "")
        elif tag == "script":
            self.scripts.append(found)
        elif tag == "meta":
            self.metas.append(found)

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._open is not None:
            kind, href = self._open
            if kind == "link":
                self.links.append((href, data))
            else:
                self.untraced.append(data)
            self._open = None


def result(
    answer: AgentAnswer | None,
    queries: list[QueryRecord],
    stop_reason: str = "answered",
    **extra: Any,
) -> AgentResult:
    steps = [ToolStep(tool="run_sql", ok=True, sql=q.sql, row_count=len(q.rows)) for q in queries]
    return AgentResult(
        answer=answer, stop_reason=stop_reason, steps=steps, queries=queries, **extra
    )


def report(agent_result: AgentResult, question: str = "Total revenue?") -> Page:
    return Page(render_report(agent_result, question=question, as_of="2026-06-28"))


def test_an_answer_number_links_to_the_cell_that_produced_it() -> None:
    page = report(result(AgentAnswer("number", 350), [BY_CHANNEL, TOTAL]))

    assert page.links == [("#q2-r1-c1", "350")]
    assert "q2-r1-c1" in page.ids
    assert "Total revenue?" in page.text
    assert TOTAL.sql in page.text


def test_a_percent_answer_links_to_its_ratio() -> None:
    page = report(result(AgentAnswer("percent", 71.4), [SHARE]))

    assert page.links == [("#q1-r1-c1", "71.4")]


def test_numbers_in_a_table_answer_link_and_text_cells_do_not() -> None:
    value = {"columns": ["channel", "revenue"], "rows": [["web", 250], ["shop", 100]]}

    page = report(result(AgentAnswer("table", value), [BY_CHANNEL]))

    assert page.links == [("#q1-r1-c2", "250"), ("#q1-r2-c2", "100")]
    assert "web" in page.text


def test_numbers_in_the_explanation_link_when_a_query_returned_them() -> None:
    reason = "Web brought 250 of 1,000 on 2026-06-01, so 71.4% of the total."

    page = report(result(AgentAnswer("number", 350, reason), [TOTAL, BY_CHANNEL, SHARE]))

    assert ("#q2-r1-c2", "250") in page.links
    assert ("#q3-r1-c1", "71.4") in page.links
    assert page.untraced == ["1,000"]
    # A date is not a number to trace.
    assert not any("2026" in text for _, text in page.links)
    assert "2026" not in "".join(page.untraced)


def test_a_withheld_answer_says_why_and_shows_no_value() -> None:
    page = report(
        result(None, [TOTAL], stop_reason="unverified", error="the answer has no query behind it")
    )

    assert page.links == []
    shown = " ".join(page.text)
    assert "unverified" in shown and "the answer has no query behind it" in shown


def test_queries_that_do_not_support_the_answer_are_still_listed() -> None:
    page = report(result(AgentAnswer("number", 350), [BY_CHANNEL, TOTAL]))

    assert BY_CHANNEL.sql in page.text
    assert "q1-r1-c2" in page.ids


def test_a_chart_embeds_its_spec_and_links_its_query() -> None:
    query = QueryResult(columns=BY_CHANNEL.columns, rows=BY_CHANNEL.rows, truncated=False)
    spec = chart_spec({"mark": "bar", "x": "channel", "y": "t", "title": "By channel"}, query)
    chart_step = ToolStep(tool="make_chart", ok=True, sql=BY_CHANNEL.sql, row_count=2, chart=spec)
    agent_result = result(AgentAnswer("number", 350), [TOTAL])
    agent_result = AgentResult(
        answer=agent_result.answer, steps=[*agent_result.steps, chart_step], queries=[TOTAL]
    )

    html = render_report(agent_result, question="Revenue by channel?", as_of="2026-06-28")
    page = Page(html)

    assert "chart-1" in page.ids
    assert any(s.get("type") == "application/json" for s in page.scripts)
    assert "By channel" in html and BY_CHANNEL.sql in page.text


@pytest.mark.security
def test_data_and_model_text_cannot_inject_markup_or_scripts() -> None:
    evil = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"
    query = QueryRecord(
        sql=f"SELECT '{evil}' AS note, 1 AS n", columns=["note", "n"], rows=[[evil, 1]]
    )
    spec = chart_spec(
        {"mark": "bar", "x": "note", "y": "n", "title": evil},
        QueryResult(columns=query.columns, rows=query.rows, truncated=False),
    )
    steps = [
        ToolStep(tool="run_sql", ok=True, sql=query.sql, row_count=1),
        ToolStep(tool="make_chart", ok=True, sql=query.sql, row_count=1, chart=spec),
    ]
    agent_result = AgentResult(
        answer=AgentAnswer("text", evil, reason=evil), steps=steps, queries=[query]
    )

    html = render_report(agent_result, question=evil, as_of="2026-06-28")
    page = Page(html)

    assert "<script>alert" not in html and "<img" not in html
    executable = [s for s in page.scripts if s.get("type") != "application/json"]
    # Pinned library scripts with integrity hashes, and the one inline script that draws charts.
    external = [s for s in executable if s.get("src")]
    assert external and all(
        (s.get("src") or "").startswith("https://cdn.jsdelivr.net/npm/") and s.get("integrity")
        for s in external
    )
    assert len(executable) - len(external) == 1
    policy = next(m.get("content") or "" for m in page.metas if m.get("http-equiv"))
    assert "default-src 'none'" in policy and "unsafe-eval" not in policy


# Lowest versions free of the high advisories npm audit reports (expression injection to XSS).
SAFE_CHART_VERSIONS = {
    "vega": (6, 2, 0),
    "vega-lite": (6, 0, 0),
    "vega-embed": (7, 0, 0),
    "vega-interpreter": (1, 2, 1),
}


@pytest.mark.security
def test_chart_libraries_are_versions_without_known_advisories() -> None:
    query = QueryRecord(sql="SELECT 'web' AS c, 1 AS n", columns=["c", "n"], rows=[["web", 1]])
    chart = chart_spec(
        {"mark": "bar", "x": "c", "y": "n"},
        QueryResult(columns=query.columns, rows=query.rows, truncated=False),
    )
    steps = [ToolStep(tool="make_chart", ok=True, sql=query.sql, row_count=1, chart=chart)]
    page = report(AgentResult(answer=AgentAnswer("number", 1), steps=steps, queries=[query]))

    sources = [s.get("src") or "" for s in page.scripts if s.get("src")]
    pinned = {}
    for src in sources:
        name, _, rest = src.removeprefix("https://cdn.jsdelivr.net/npm/").partition("@")
        pinned[name] = tuple(int(part) for part in rest.split("/")[0].split("."))
    assert {"vega", "vega-lite", "vega-embed"} <= set(pinned)
    for name, version in pinned.items():
        assert version >= SAFE_CHART_VERSIONS[name], f"{name} {version} has known advisories"


@pytest.mark.parametrize(
    ("written", "value"),
    [
        ("1,000", 1000.0),
        ("1.234.567", 1234567.0),
        ("1,234.5", 1234.5),
        ("1.234,5", 1234.5),
        ("71,4", 71.4),
        ("0.714", 0.714),
        ("-2.5", -2.5),
    ],
)
def test_numbers_in_either_style_link_to_their_value(written: str, value: float) -> None:
    query = QueryRecord(sql="SELECT 1", columns=["v"], rows=[[value]])

    page = report(result(AgentAnswer("text", "x", reason=f"It was {written}."), [query]))

    assert page.links == [("#q1-r1-c1", written)]


def test_large_numbers_are_grouped_and_years_are_not() -> None:
    query = QueryRecord(
        sql="SELECT 2026, 3630000", columns=["year", "revenue"], rows=[[2026, 3630000]]
    )

    page = report(result(AgentAnswer("number", 3630000), [query]))

    assert page.links == [("#q1-r1-c2", "3,630,000")]
    assert "2026" in page.text


def test_a_year_in_the_explanation_is_text_unless_a_query_returned_it() -> None:
    reason = "Revenue for June 2026 was 350."

    page = report(result(AgentAnswer("number", 350, reason), [TOTAL]))

    assert page.untraced == []
    assert ("#q1-r1-c1", "350") in page.links
    counted = QueryRecord(sql="SELECT 2026 AS n", columns=["n"], rows=[[2026]])
    assert ("#q1-r1-c1", "2026") in report(
        result(AgentAnswer("text", "x", reason), [counted])
    ).links
