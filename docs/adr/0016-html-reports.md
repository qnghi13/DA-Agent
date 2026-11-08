# 0016: Reports link every number to the query cell the agent read

**Status:** Accepted, 2026-11-08

**Context:** The plan asks for standalone HTML reports in which every number traces to the query
that produced it. A report shows text from the model and values from uploaded files, so it is an
injection surface, and charts need a JavaScript library to draw.

**Decision:**
- `AgentResult.queries` keeps each successful `run_sql` with the columns and bounded rows the model
  saw, read from the run's messages after it ends. State does not grow, old checkpoints still
  load, and the report shows the rows the answer was computed from, not a rerun.
- `render_report` (`src/da_agent/reports/html.py`) is a pure function of the result. Each number in
  the answer, and each number in its explanation that a query returned, links to the cell it
  matches, using the same rounding rule as verification. Each cell has its own anchor and is
  highlighted when the link is followed. Answer numbers that match no cell are marked and counted.
  Numbers in the explanation are read in both 1,234.5 and 1.234,5 styles, and dates are skipped.
- Charts use Vega 6 builds from jsDelivr (vega 6.4.0, vega-lite 6.5.0, vega-embed 7.3.0),
  pinned by version and SRI hash. vega-embed bundles the expression interpreter and uses it when
  `ast` is set, so the Content Security Policy allows only those scripts and the one inline
  script, by its hash, with no `unsafe-eval`. Specs sit in `application/json` blocks with `<`, `>`
  and `&` escaped. Every other piece of text is HTML-escaped.
- Without network the charts are not drawn, but each chart's data and query stay in the page.

**Consequences:**
- A report checked in headless Chrome draws its chart under the policy. Tests cover links,
  escaping, the policy, and number reading.
- 2026-11-08: moved from Vega 5.33 with `vega-interpreter` 1.0.5 to Vega 6, because npm audit
  reports high expression-injection advisories for vega up to 6.1.2 and vega-interpreter before
  1.2.1. A security test fails if a pinned library is below its first fixed version. The specs
  were rendered to SVG with Vega 6 and the interpreter in Node, not yet in a browser. Upgrading
  means checking the three builds together; the web UI uses the same versions from npm.
- An explanation can contain numbers that are not query results, such as "top 3". They are
  marked as not traced but not counted, since only the answer value is verified.

**Rejected:**
- Rerunning the queries when the report is built: needs the dataset and the sandbox, and could
  show different rows from those the answer used.
- Inlining the Vega libraries (about 830 KB): a fully offline file, but large; it can be added as
  an option if offline reports are needed.
- Rendering charts to images on the server: needs a headless browser or a Node renderer in the
  Worker, and loses tooltips.
