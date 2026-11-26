# 0051: Fixed analyses: the model names one, code computes it

**Status:** Accepted, 2026-11-26. Design: [analysis tools](../design/2026-11-26-analysis-tools.md).

**Context:** The agent answered "how much" and "which" with SQL. People who do not work with
data also ask why a total changed, whether it is rising, what comes next, what is unusual, and
whether two things move together. One SQL query answers these badly. On the `archive/python-analysis`
branch (2026-11-13, never on main), free-form Python in the sandbox was misused by
`gpt-oss-20b`: it opened its own empty database, overwrote helpers, and copied numbers into code.
A fixed tool, where the model names the kind and code computes, was called on 4/4 analysis
questions once the plan had a required field naming the analysis. Major BI products work the same
way: Tableau Pulse, Power BI "Explain the increase", Snowflake `TOP_INSIGHTS`, and ThoughtSpot
change analysis compute with fixed statistical methods and use a model only for words.

**Decision:**
- **One tool, five kinds.** `analyze(kind, sql, x, y, dimensions?, before?, after?, periods?)`:
  - `explain_change`: new. The change in a sum or count between two date ranges, split by up to 4
    dimensions.
  - `trend`, `forecast`, `correlation`, and `outliers`: ported from the archive with the same
    methods: a least-squares slope with p < 0.05, ETS with a 95% interval, Pearson and Spearman,
    and a modified z-score above 3.5.
- **`explain_change` refuses what would mislead.** Its checks, in the Worker and in the sandbox:
  - **Additive `y` only.** `y` must be a SUM or a COUNT without DISTINCT, checked with sqlglot
    before any sandbox starts. Averages and ratios do not split into parts.
  - **Whole periods.** A range may not cut a period of `x` in two.
  - **Inside the data.** A range may not run past the data's coverage.
  - **Same length.** The two ranges must be as long, or cover the same number of whole calendar
    months (September against October, Q1 against Q2). So part of a month is never compared with
    a whole one; the error names the same-days range to use instead.
  - **Rows in each range.** Rows without a date are left out with a note, not refused.
  - **One value ranks last.** A dimension with one value, such as a branch the query already
    filtered to, holds all of the change and says nothing, so it never leads.

  `y` is followed into CTEs and subqueries, so an average computed inside one is still refused.

  Each dimension's parts add up exactly to the change, and dimensions rank by the largest share
  one segment carries. The tool result tells the model the parts show where the change happened,
  not why.
- **Computed in the sandbox.** A new `StatsRun` request runs on the Runner (`POST /v1/stats`),
  in a fresh container with the dataset read-only and the query limits. The image adds numpy,
  pandas, scipy, and statsmodels, pinned with hashes. No model-written code runs; the model's SQL
  passes the guard first.
- **When it is offered and chosen.** Product runs offer it (`analysis` in the run payload, true
  from the API). Eval runs do not unless `--analysis` is given, and then the manifest says so.
  - When it is offered, the plan tool gets a required `analysis` field, and code turns a named kind
    into a directive for the loop.
  - A run can force a kind (`analysis_kind`), as the web's buttons do. The directive tells the loop to answer with `run_sql` when `analyze` refuses an average.
  - With analysis off, every request is byte-identical to before. A test pins the hash.
- **Results are query records.** A successful analysis becomes a `QueryRecord` with its SQL, its
  table as rows, and an `analysis` field (kind, arguments, summary, notes, chart). Every evidence
  path reads analyses unchanged:
  - numbers trace;
  - the period, fan-out, and quarantine checks read the analysis SQL;
  - reports and plain explanations cover it.
  No graph state changed.
- **Charts are built by code** from the analysis table (ADR 0015): a waterfall for
  `explain_change`, a fitted line for `trend`, and a band for `forecast`. There is no transform;
  the waterfall's bar ends are computed.
- **In the web:**
  - an analysis card with a headline written by the web in the UI language, the chart, the
    segments, translated notes, a fixed line that this is where it changed and not why, and the
    method;
  - a "Why did it change?" button under answers over time;
  - a chip that drills into the leading segment.
- **Repeats.** An identical `analyze` call that already returned is answered from the
  conversation (error kind `repeat`); at most 4 analyses per run.

**Consequences:**
- **Image and latency.** The sandbox image grows from 293 MB to 787 MB. A small analysis takes
  2.75 s against 1.67 s for a query (median of 3, WSL).
- **Memory.** One million rows of `explain_change` fit in the 512 MB query limit.
- **Checked in the running app** (Groq `gpt-oss-20b`, demo shop, $0.006):
  - the button asked May against June, and the tool split the change by channel;
  - the card showed GrabFood carrying the rise;
  - the model's own text still said "mainly due to" before adding the caveat, so the card's
    fixed line is the guard, not the model.
- **What the user sees while measuring.** A synthetic shop with planted effects
  (`eval/analysis/`, 16 questions) measures the tool at $0 for the build. Real runs cost money
  and wait for approval.
- **Not covered.** Changes in averages and rates (mix decomposition), what-if questions, and
  picking a chart point are not covered.

**Measured** (2026-11-26, Groq `gpt-oss-20b`, $0.168 in all):
- **The analysis eval** (`eval/analysis/`, 16 questions, 3 attempts, $0.042):
  - the right kind on 8/10 every attempt;
  - correct analysis answers 6, 8, and 7 of 10, so the bar of 7 was missed once;
  - every control ran no analysis (6/6);
  - 11/16 questions were right in all three attempts.
- **What failed:**
  - The Q1 drill-down was answered right with `run_sql` (no analysis) 3/3. The question reads as
    a ranking.
  - "Is drinks revenue rising?" is ambiguous in the generated data: the long trend rises, but the
    last two months fall from the other planted effects. The eval needs a cleaner question.
  - Twice the model passed too few dimensions and missed the branch.
  - One run gave up after a correct analysis, with six malformed answers.
- **The same-length check worked:** twice refused, the model fixed its ranges to 1-26 October
  against 1-26 November.
- **`smoke`** (30 DataMate questions, graph agent): +17 without analysis against +14 with it.
  One run each, within the noise of a single run (note 19), and the changed questions moved both
  ways. Measure with pass^k before calling it harm or help.

**Rejected:**
- **Free-form Python (`run_python`):** misused by the target model in the archive's trials;
  verification cannot read code; a person who does not read code cannot check it.
- **A decision tree over segments (as Snowflake does):** harder to explain than parts that add
  up.
- **Computing in the worker:** needs the numeric libraries there, and loads every row out of the
  sandbox.
- **A wider SQL surface (`regr_slope`, window functions):** covers simple trends only.
