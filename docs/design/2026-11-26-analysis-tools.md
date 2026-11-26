# Analysis tools: the model names an analysis, code computes it

**Status:** implemented on branch `feat/analysis-tools` (2026-11-26), recorded in
[ADR 0051](../adr/0051-fixed-analyses.md); the real-model eval waits for the user. First of three projects for deeper analysis:
B (this document), then C (insight cards), then D (multi-step deep analysis).

## Why

Today the agent answers "how much" and "which": one or a few SQL queries, a value, a chart. People
who do not work with data also ask:

- **Why did it change?** "Vì sao doanh thu tháng 10 giảm?"
- **Is it rising or falling, and what comes next?** "Doanh thu đang tăng hay giảm? Tháng sau
  khoảng bao nhiêu?"
- **Is anything unusual?** "Có ngày nào bất thường không?"
- **Do these move together?** "Giảm giá nhiều thì bán được nhiều hơn không?"

A single SQL query answers these badly or not at all, and a small model that improvises the method
gets it wrong. Work on the `archive/python-analysis` branch (2026-11-13) showed this:

- Free-form Python (`run_python`) was misused by `gpt-oss-20b`. It opened its own empty database,
  overwrote helpers, and copied numbers into code. `gpt-oss-120b` used the helpers but chose naive
  methods.
- A fixed tool, where the model names the kind, a query, and columns and code computes, was called
  on 4/4 analysis questions once the plan had a required field naming the analysis.

The users are non-technical first, for example a shop owner or a sales manager reading their own
sales data. They cannot judge a method or read code, so the method must be right by construction
and the answer must say plainly what it does and does not show.

## Decided by the user (2026-11-26)

- Build all three of B, C, and D, in that order. C and D reuse B's computations.
- The answer sketch below is accepted as the target for "why did it change".

Everything else in this document is a proposal (Claude's call) and can be corrected.

```
Doanh thu tháng 10/2026: 82,4 tr, giảm 18,1 tr (−18%) so với tháng 9.

Phần giảm tập trung ở:
  Chi nhánh:  Q1 −12,3 tr (68% mức giảm) · Q3 −4,0 tr · Q7 +1,2 tr
  Nhóm hàng:  Mỹ phẩm −9,8 tr (54%) · Gia dụng −5,1 tr · ...
[waterfall chart: September → each branch → October]

Đây là chỗ doanh thu giảm, không phải lý do. Dữ liệu không cho biết lý do
(hết hàng, thời tiết…).
Cách tính: tổng "Thành tiền", bỏ đơn "Đã hủy", so 01–31/10 với 01–30/09.
[Xem Q1 theo sản phẩm →]
```

## Goals, non-goals, anti-goals

**Goals.**

- One tool, `analyze`, with five kinds: `explain_change` (new), plus `trend`, `forecast`,
  `correlation`, and `outliers` ported from the archive branch with their methods unchanged.
- In chat, the agent uses `analyze` for questions that need it, without the user naming a method.
- In the web, a "Why did it change?" button under time-series answers, and a drill-down chip
  under an `explain_change` answer. Both work in English and Vietnamese.
- Every number shown from an analysis traces to the analysis result, and the SQL behind an
  analysis passes the same checks as the SQL behind any answer.
- Measured on a synthetic dataset with planted effects, so the right answers are known in
  advance.

**Non-goals** (some are later projects):

- Insight cards without a question (project C), and multi-step reports (project D).
- Measure and dimension roles in the dataset spec (needed by C, not by B).
- Explaining changes in averages, ratios, or distinct counts (rate and mix decomposition). The
  first version splits sums and counts only.
- What-if questions and scenario analysis.
- Significance tests between groups. "Which channel sells more" stays a SQL question.
- Clicking a point on a chart to pick the periods to compare. The button compares the last two
  points.
- `run_python`. It stays on the archive branch.
- Analyses in eval runs by default. DataMate prompts stay byte-identical unless a flag turns
  analysis on.

**Anti-goals** (a failure even if the feature works):

- An answer card presents a segment as the cause of a change. The card says where the change
  happened, never why.
- A part of a period compared with a whole period without saying so, such as November 1–26
  against all of October.
- A number on an analysis card that no tool result produced.
- Questions that need no analysis get slower or worse because the tool is offered.
- A method choice left to the model.

## Approaches considered

| | Approach | Verdict |
|---|---|---|
| A | The model writes Python in the sandbox (code-interpreter style) | Rejected as the main path: tried with `gpt-oss-20b` and misused; coverage of verification is weak; a non-technical user cannot check code |
| B | The model names an analysis kind, a query, and columns; code computes with a tested method (this document) | First: proven on the current model, keeps code-first verification (ADR 0007), the same pattern as charts (ADR 0015) |
| C | Code scans measures × dimensions × periods at build time and shows the top insights | Second: reuses B's computations, needs measure roles in the spec |
| D | A plan splits an open question into 3–6 sub-questions, each answered by the agent with B's tools, then a report | Third: without B its sub-questions are only SQL; costs 4–8 times a run; hardest to evaluate |

## Prior art, and the pattern this follows

Major BI products compute analyses with deterministic statistics and use an LLM only for language
or orchestration:

- Tableau Pulse: period-over-period change, top drivers and detractors, and unexpected values,
  each from a fixed statistical model, with an LLM summary.
- Power BI "Explain the increase/decrease" and Snowflake `TOP_INSIGHTS`: they split a metric's
  change between two points into the segments that contributed.
- ThoughtSpot change analysis: iterative drill-down into a contributing value.
- InsightPilot (EMNLP 2023): the LLM issues analysis actions to an insight engine, so it does not
  compute or invent numbers.
- Adtributor (NSDI 2014) ranks dimensions by how much of a change their segments explain.

This design follows the **trusted functions** pattern: the model chooses what to analyse and code
decides how.

## Starting point: the archive branch

`archive/python-analysis` forked before ADRs 0032–0050, so it cannot be merged. Its ADR numbers
collide with main's, and the graph has changed since. **Port** these parts, with their tests:

- `src/da_agent/storage/stats.py` and `tests/unit/test_stats.py`: methods, period inference,
  and unfinished-period handling.
- `StatsRun`, the Runner's `POST /v1/stats` endpoint, and `SandboxDataset.stats`, together with
  their runner and service tests.
- The required plan field and the directive written by code (`plan_spec`, `plan_notes` in
  `planning.py`).
- The `analyze` tool spec and `_analyze_on` in `tools.py`.
- The repeat guard: an identical `analyze` call that already returned a result is answered from
  the conversation (error kind `repeat`). Here it applies to `analyze` only, so tool behaviour in
  eval runs does not change.

Do not port `run_python`, `python_analysis.py`, or the `DA_AGENT_PYTHON_TOOL` flag. The
`functions.` name cleanup already exists on main (ADR 0049).

## Design

### The tool

`analyze(kind, sql, x, y, dimensions?, before?, after?, periods?)`:

| Kind | Asks | Input | Method |
|---|---|---|---|
| `explain_change` | Where did a sum or count change between two periods? | `x` dates, additive `y`, 1–4 `dimensions`, `before` and `after` ranges | Additive split by segment (below) |
| `trend` | Is `y` rising or falling? | `x` one date per regular period | Least-squares slope, p < 0.05 for a direction |
| `forecast` | The next 1–12 periods of `y` | Same, plus `periods` | ETS, additive trend, damped from 8 periods, seasonal from two full seasons, 95% interval |
| `correlation` | Do `x` and `y` move together? | Two numeric columns | Pearson and Spearman, p-value, strength word |
| `outliers` | Which rows have an unusual `y`? | `x` labels rows | Modified z-score from median and MAD, \|z\| > 3.5 |

- `sql` passes the SQL guard in the Worker. Code then reads every row it returns, up to one
  million, in a fresh sandbox.
- Errors are a `StatsError` whose message tells the model what to change. They come back as tool
  results with error kind `analysis`, counted like other kinds (ADR 0031).
- At most 4 successful analyses per run (my call; charts allow 3). Each call counts as a tool
  iteration.

### `explain_change`

**Input.** One SELECT that returns a date column `x`, a measure `y`, and the dimension columns,
for example:

```sql
SELECT CAST(order_date AS DATE) AS day, branch, category, SUM(amount) AS revenue
FROM orders WHERE status <> 'Đã hủy' GROUP BY ALL
```

`before` and `after` are `{start, end}` ISO dates, both ends included. The model can copy them
from the periods note that code already adds to the question (`periods.py`, which includes
"last month, same days").

**Checks in code**, each a `StatsError` naming the fix:

1. `y` must be additive. In the SELECT, `y` is `SUM(...)`, `COUNT(*)`, `COUNT(col)`, or a plain
   column that code will sum. `AVG`, `COUNT(DISTINCT ...)`, `MIN`, `MAX`, and arithmetic across
   aggregates are refused with "explain_change splits sums and counts; ...". sqlglot checks this
   in the Worker before the sandbox runs.
2. `x` holds dates. The grain is inferred as in `stats._grain`. A range boundary that cuts inside
   one `x` period is refused: a monthly `x` cannot compare November 1–26.
3. `before` ends before `after` starts.
4. Both ranges lie inside the data's coverage of the column `x` comes from (`AgentContext.ranges`,
   the same data the period check uses). A range that runs past the end of the data is refused,
   naming the last date and suggesting the same days of the earlier period. This blocks
   comparing a partial month with a whole one. When code cannot resolve `x` to a source column,
   it uses the min and max of `x` in the result and adds a note.
   This check is stricter than verify's period check, which only catches a filter entirely
   outside the data. The SQL of an `explain_change` usually has no date filter, because the
   ranges are arguments, so this check is the one that guards them. `AgentContext.ranges` is
   keyed by column name, the same keys `verify.date_bound` resolves.
5. Each range has rows. A before total of 0 leaves the percent change undefined, with a note.

**Computation.**

- `before_total`, `after_total`, `change`, `change_percent`.
- For each dimension `d` and value `v`: `before_v`, `after_v`, `change_v`, and
  `share_v = change_v / change`. A value with no rows in one period is marked `new` or `gone`.
  Blank values are grouped as `(blank)`.
- Invariant, tested: for every dimension, the `change_v` sum to `change`. Shares can be negative
  or above 100% when segments offset each other.
- Dimensions are ranked by the largest share any one of their segments holds in the direction of
  the change. The answer leads with that segment ("Q1: 68% of the drop").
- When `change` is 0 or near it (under 1% of `before_total`), shares are not computed. Dimensions
  are ranked by the sum of `|change_v|`, with the note "the total barely moved; segments offset
  each other".
- Each dimension keeps the 10 segments with the largest `|change_v|`, plus one `other` row, so
  its rows still sum to the change.

**Result** (the same envelope for every kind):

```
{kind, summary, table: {columns, rows, truncated} | null, notes: [{code, params, text}]}
```

For `explain_change`:
- `summary` holds the two ranges with their totals, the change, the percent, the top segment, and
  the ranking.
- `table` is one long table,
  `[dimension, segment, before, after, change, share_percent, status]`. It starts with a total
  row, then lists the dimensions in rank order. One table keeps every number in one query record
  (see below).
- The model reads `text`. The web translates `code` with `params`. The tool content ends with one fixed line: "These are
where the change happened, not why it happened; say so in the answer."

### Where it runs

As in the archive: `StatsRun` in a fresh sandbox with the same flags as a query, the dataset
mounted read-only, and the query memory limit (512 MB).

- The sandbox image adds `numpy`, `pandas`, `scipy`, and `statsmodels`, pinned with hashes. The
  archive measured 293 MB → 786 MB, and about 1.6 s per analysis against 1.1 s for a query.
- The builder checks that one million rows fit in 512 MB. If they do not, the builder adds a
  separate stats memory setting rather than lowering the row cap silently.

### When it is offered and chosen

- **Run flag:** `analysis` in the run payload, true by default for API runs like `charts`. Eval
  runs have it off unless `--analysis` is passed. Off means the tool list, the plan tool, and every
  prompt are byte-identical to today, checked by hashes in a test. Offered only on datasets that
  can run it (`can_stats`).
- **Plan field:** when analysis is offered, `submit_plan` has a required `analysis` enum (`none`
  plus the five kinds), and code turns a kind into a directive for the tool loop, as in the archive.
- **Forced kind:** the run payload can carry `analysis_kind`. Code then sets the plan's analysis
  field to that kind whatever the model chose, and the plan still writes its steps. The web
  buttons use it. `analysis_kind` with `analysis` off is a 422.
- **Repeat guard:** for `analyze` only (see Starting point).

### Results, state, and evidence

- **Recorded on the query record** (settled in the plan): a successful `analyze` call becomes a
  `QueryRecord` in `AgentResult.queries`. Its SQL is the analysis SQL, its columns and rows are
  the analysis table, and it has a new optional `analysis` field (`kind`, `arguments`, `summary`,
  `notes`, and the chart). `AgentResult.analyses` lists them.
  - The record is built from the tool message, as query records are, so the graph state does not
    change.
  - Old results and checkpoints load with the field absent.
- **Part of the evidence:** analyses join the evidence everywhere queries are evidence. Their
  numbers (table and summary) count as query results. As a result:
  - numbers in the answer trace to analysis results;
  - the period, join fan-out, and quarantine checks run on the analysis SQL (closing the gap ADR
    0033 left);
  - the plain explanation (ADR 0035) and the HTML report cover it.
- **Which card shows:** the card under an answer is for the evidence analyses only, not for
  analyses the model ran along the way.

### Charts

Built by code from the analysis result, never by the model (ADR 0015):

- `explain_change`: a waterfall from the before total through the top dimension's segments to the
  after total.
- `trend`: the series with its fitted line.
- `forecast`: the series, then the forecast with its 95% band.
- `correlation` and `outliers`: no chart in the first version. An outliers chart would need
  every row, and the result holds only the outlying ones.

Analysis charts do not count against `make_chart`'s limit.

### Web

- **Analysis card** under the answer, in `run-message.tsx`:
  - a headline sentence written by the web from `summary`, in the UI language;
  - the chart;
  - the top dimension's segments, with other dimensions collapsed;
  - translated notes;
  - for `explain_change`, the fixed line "This is where it changed, not why; the data does not say
    why";
  - "How it was computed": the plain explanation of the analysis SQL plus one method sentence.
- **"Why did it change?" button:**
  - **When it shows:** under an answer whose chart or evidence query has a date `x` with at least
    two points, on a dataset that runs analyses.
  - **The question it sends:** a follow-up in the same conversation, for example "Vì sao
    {measure} thay đổi từ {second-to-last label} sang {last label}?", with
    `analysis_kind=explain_change`.
  - **How the measure and filters carry over:** the conversation memory already gives the model
    the last query (ADR 0030). The model picks the ranges and code checks them, so an unfinished
    last period becomes a same-days comparison.
- **Drill-down chip** under an `explain_change` card, when the query had another dimension:
  "Xem {top segment} theo {next dimension} →". It sends a follow-up with the same forced kind,
  asking to filter to that segment and split by the remaining dimensions.
- **Translations:** new strings go in both dictionaries in `web/src/i18n`.

### API

- Run creation accepts `analysis` (bool) and `analysis_kind` (enum, validated).
- A run read returns `analyses`.
- No new route on the API. The Runner gains `POST /v1/stats`, which takes the same token as its
  other routes.

### Telemetry, limits, security

- **Telemetry:** an `analyze` span carries the kind, rows read, duration, and error kind. The
  agent manifest fingerprint changes when the tool spec changes, which it already does for any
  spec. No new model calls, so the cost ledger is unchanged.
- **What the model controls:**
  - SQL, which passes the guard;
  - column names, validated against the result;
  - the kind, an enum;
  - date ranges, parsed and validated;
  - `periods`, bounded 1–12.
  No model-written code runs. The threat model records the new Runner endpoint and the added
  sandbox libraries.
- **Cell values:** dimension values are cell values. They are data in the prompt, text in the UI,
  and masked in traces under the same rules as query results.

## Evaluation

1. **Code ($0).**
   - The ported stats tests.
   - `explain_change` tests: additivity, new and gone segments, offsetting changes, a flat total,
     grain mismatch, a range past the data, non-additive `y`, the `other` row, output bounds.
   - The byte-identical test with analysis off.
2. **Synthetic dataset, `eval/analysis/`** (my call on the contents):
   - A seeded generator, committed, with no DataMate data: a shop over 18 months, with 3
     branches, 4 categories, and 3 channels.
   - Planted effects:
     - October revenue −20%, about 70% of it from one branch;
     - a steady rise in one category;
     - a yearly peak (for the forecast);
     - three outlier days;
     - discount correlated with quantity.
   - The generator prints the expected answers.
   - The approved spec is saved in `eval/analysis/` once, so a run builds the dataset with no
     model call.
   - The data ends on a fixed date, and the question set fixes `as_of`, so "this month" means
     the same thing on every run.
3. **Question set:** about 16 questions, mostly Vietnamese.
   - 10 need an analysis: 2 per kind, including one same-days comparison of an unfinished month
     and one drill-down.
   - 6 controls need no analysis.
   - Each question has a code check: the kind called, the top segment, the direction, or a value
     within tolerance.
4. **Real model, Groq `gpt-oss-20b`** (ask before running):
   - The set three times (pass^k, ADR 0040), about $0.10–0.30 in all.
   - Then `smoke` (30 DataMate questions) with analysis on against off, about $0.05–0.10, to
     check for harm.
5. **Exit criteria** (my call):
   - right kind called on ≥ 8/10;
   - correct on ≥ 7/10;
   - no answer with a number that does not trace;
   - controls call no analysis on ≥ 5/6;
   - `smoke` not worse beyond noise (one run moves about 9 points; note 19).

## Build order

1. Port stats: sandbox dependencies, `StatsRun`, the Runner endpoint, `SandboxDataset.stats`,
   tests.
2. `explain_change` computation and its checks, with tests.
3. The `analyze` tool, the plan field and directive, run flags (worker, API), the forced kind,
   and the repeat guard.
4. Evidence: analyses in query records and numbers, the SQL checks on analysis SQL,
   `AgentResult.analyses`, the plain explanation, and the report.
5. Charts from analyses.
6. Web: the card, the button, the chip, translations.
7. Synthetic dataset, question set, and eval runner. Then the real runs, after asking.
8. ADR 0051, `architecture.md`, `threat-model.md`, learning note 28, `progress.md`.

## Left to the builder

- Tie-breaks in the dimension ranking, and the exact "near 0" threshold.
- How `x` is resolved to its source column for the coverage check.
- The Vega-Lite details of each chart.
- How the eval runner for `eval/analysis/` shares code with `eval/datamate/run.py`.

## Risks

- **Weak dimensions:** the model may leave out the dimension that explains the change. The eval
  measures this. Code could later suggest the low-cardinality columns of the tables the query
  reads.
- **SQL instead of the tool:** the model may answer a "why" question with SQL anyway, as the
  archive's trend question did. The plan field and the forced kind are the defences, and the eval
  counts how often it happens.
- **Averages refused:** "why did the average order value drop" is refused in this version. The
  model falls back to SQL. Rate and mix decomposition is a later addition.
- **Image size:** the sandbox image grows about 500 MB.

## Known gaps after review (2026-11-26)

Found by the whole-branch review, graded minor, not fixed yet:

- **Coverage:**
  - A monthly `x` with unknown coverage uses the start of the last period as the data's end, so
    ranges over the last month are refused.
  - The coverage fallback does not follow CTEs, subqueries, or `SELECT *`, and takes every date
    column of every table read, so an unrelated date column can refuse valid ranges.
- **Additivity:** `SUM(x) FILTER (WHERE ...)` and `ROUND(SUM(x), 2)` are refused with a confusing
  message. `FILTER` and `ROUND` should be treated as wrappers.
- **Segment names:** they are not capped in length, and a literal `(blank)` or `(other)` cell
  merges with the markers.
- **API:** the spec says a run read returns `analyses`; the API returns them inside
  `queries[].analysis`.
- **Cards:** small summary integers (`periods`, `horizon`, `threshold`) count in evidence
  matching, so an analysis run along the way can attach a card.
- **Grain:** a daily `x` whose dates are all the 1st of a month reads as monthly.
- **Eval:**
  - there is no question where the data ends before `as_of`;
  - there is no question about a non-additive measure;
  - the drinks trend question is ambiguous (see ADR 0051, Measured).
