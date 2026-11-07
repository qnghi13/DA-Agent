# 0015: Charts are Vega-Lite specs that code builds from a query

**Status:** Accepted, 2026-11-07

**Context:** The plan says charts are validated Vega-Lite specs with no code execution. A Vega-Lite
spec written by the model can still do harm or mislead: `data.url` fetches from anywhere, `calculate`
and `filter` run Vega expressions in the viewer's browser, and inline `data.values` can hold numbers
no query returned. Validating every part of a model-written spec against the Vega-Lite schema
would need the full schema and an allowlist of its features.

**Decision:**
- `make_chart` takes `sql`, `mark` (`bar`, `line`, `point`, `area`, `arc`), the result columns
  `x`, `y`, and an optional `color`, and a `title`. Code runs the query on the same read-only
  dataset as `run_sql`, then builds the spec: inline rows from the result, one encoding per named
  column, and a field type read from the values. Any other key the model sends is ignored.
- `y` must be numeric. A result with no rows, or more rows than the query limit, is an error that
  asks the model to aggregate. Column names with `.` or `[]` are escaped, because Vega-Lite reads
  them as nested fields.
- The model gets back only `{"status": "drawn", "mark", "rows"}`, not the rows. Numbers in the
  answer still come from `run_sql`, so the checks of ADR 0013 and 0014 do not change.
- The spec is stored on the `ToolStep` with its SQL, so a chart keeps its evidence, a resume does
  not draw it twice, and old checkpoints still load. `AgentResult.charts` lists them. A run draws
  at most 3.
- The tool is offered only when the run asks for charts (`run_graph(charts=True)`). Eval runs do
  not, so their prompts and scores do not change.

**Consequences:**
- A spec cannot fetch data or run expressions, and every value in it came from a query. A test
  sends a data URL, a transform, and an HTML title, and none of them reach the spec except the
  title as plain text, cut to 120 characters.
- Charts are limited to one layer with x, y, and color: no layered, faceted, or binned charts,
  and no sorting or axis formats. These can be added as more named arguments when a report needs
  them.
- Rows are bounded by the dataset's row limit (50 by default), so a long daily series has to be
  grouped by week or month.
- Specs live in state until an artifact store exists. Three charts of at most 50 rows keep it
  small.

**Rejected:**
- The model writes a full Vega-Lite spec and code validates it with the JSON schema: the schema
  allows URLs and expressions, so an allowlist would still be needed, and inline numbers would
  need their own trace check.
- The model writes plotting code (matplotlib) run in the sandbox: more expressive, but it is code
  execution, produces images that cannot be inspected, and the sandbox has no `run_python` yet.
