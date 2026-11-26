# Analysis Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An `analyze` tool whose five kinds (`explain_change`, `trend`, `forecast`,
`correlation`, `outliers`) are computed by code in the sandbox, offered in product runs, verified
like queries, and shown in the web as an analysis card with a "Why did it change?" button.

**Architecture:** The model names a kind, one SELECT, and columns. The Worker validates the
arguments and the SQL (guard and additivity) and sends a `StatsRun` to the Runner. The sandbox
runs `da_agent.storage.stats`, which reads every row and computes with fixed methods. The result
goes back to the model as a tool result. Verification treats it as a query record, so numbers
trace and the analysis SQL gets the period, fan-out, and quarantine checks. Code builds the charts.
The web renders a card from the structured result.

**Tech Stack:** Python 3.12, uv, LangGraph, DuckDB, sqlglot, pandas 3.1.1, numpy 2.5.4,
statsmodels 0.15.0 (with scipy), FastAPI, pytest; React + TypeScript + Vitest, Vega-Lite.

**Spec:** [2026-11-26-analysis-tools.md](2026-11-26-analysis-tools.md) (approved 2026-11-26).
Read it in full before Task 1. Code to port is on branch `archive/python-analysis`. Read it with
`git show archive/python-analysis:<path>`. Never merge that branch.

## Global Constraints

- **Tests and checks:** tests come first (`tdd` skill). `uv run ruff check .`,
  `uv run ruff format .`, `uv run pytest`, and in `web/` `npm test`, `npm run typecheck`, and
  `npm run lint` pass at the end of every task. Docker tests are marked `integration` and skip
  without Docker.
- **Commits:** work on branch `feat/analysis-tools`. Commit locally per task, but ask the user
  once before the first commit (CLAUDE.md: commit only when asked). Never push.
- **Analysis off is unchanged:** when analysis is off, every LLM request is byte-identical to
  `main`. The hash test from Task 1 guards this and must pass after every task.
- **Dependencies:** `pandas==3.1.1`, `numpy==2.5.4`, `statsmodels==0.15.0` go in
  `sandbox/requirements.in` and in the `dev` group of `pyproject.toml`, not the main
  dependencies, so the worker image stays as it is. Compile with the command in the header of
  `sandbox/requirements.txt`.
- **Limits:**

  | Limit | Value |
  |---|---|
  | rows read | 1,000,000 (`MAX_INPUT_ROWS`) |
  | table rows | 50 (`MAX_TABLE_ROWS`) |
  | forecast horizon | 1–12 (`MAX_HORIZON`) |
  | significance | p < 0.05 |
  | outliers | \|robust z\| > 3.5 |
  | analyses per run | 4 (`MAX_ANALYSES`) |
  | dimensions | 1–4 |
  | segments per dimension | 10, plus `(other)` |
  | "flat" | \|change\| < 1% of \|before total\| |
  | `StatsRun.timeout_seconds` | default 30, at most 60 |
  | forecast history in its table | 36 periods |

- **Method choices stay in code.** The model chooses only the kind, the SQL, the columns, the
  ranges, and `periods`.
- **Caveat on `explain_change`:** no card or prompt text calls a segment a cause. The tool content
  of every `explain_change` ends with `CHANGE_GUIDANCE`.
- **Copy and language:**
  - Every new UI string goes in `web/src/i18n/en.ts` and `vi.ts`.
  - Code, docs, and the ADR are in English. The learning note is in Vietnamese.
  - Notes are `{code, params, text}`. The model reads `text`; the web translates `code`.
- **Data:** no DataMate data or names anywhere. The eval dataset is synthetic and seeded.
- **Spend:** ask before any real LLM call, and report the cost of every eval run.

## Review Focus

1. **"Why did it change?" on an average.** A user clicks the button under "average order value
   by month". The tool refuses (not additive), and the run must still end with an answer from
   `run_sql` or an abstain, not a loop of refused calls.
   Test: Task 8 `test_a_refused_explain_change_still_lets_the_run_answer_from_sql`.
2. **Mid-month click.** On 26 November, comparing "this month" with "last month" must become
   1–26 Nov against 1–26 Oct, never a partial month against a whole one, including when the data
   ends before today.
   Tests: Task 3 `test_a_range_past_the_data_is_refused_with_the_same_days_hint` and
   `test_a_monthly_x_cannot_compare_part_of_a_month`.
3. **Real dimension values.** Accented text, blank or NULL cells, and number-typed codes all
   become segments: blanks as `(blank)`, numbers as strings, accents kept.
   Test: Task 3 `test_blank_numeric_and_accented_segments`.
4. **A product dimension with 500 values.** Keep the top 10 by \|change\| plus `(other)`, and the
   dimension still sums exactly to the change.
   Test: Task 3 `test_many_segments_keep_ten_and_other_and_still_sum`.
5. **History made before this change.** Saved run results and checkpoints without the new keys
   (`QueryRecord.analysis`, `plan_analysis`, payload `analysis`) still load, read, and resume as
   plain runs.
   Tests: Task 7 `test_old_results_without_analysis_load` and Task 10
   `test_a_job_queued_before_analysis_runs_without_it`.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `tests/unit/test_agent_graph.py` | Hash test for analysis off, graph tests with a stats fake | 1, 8 |
| `src/da_agent/storage/stats.py` | Dispatcher, query, period helpers, notes, the four ported kinds | 2 |
| `src/da_agent/storage/change.py` | `explain_change` computation and its checks | 3 |
| `sandbox/da_sandbox/__main__.py`, `sandbox/Dockerfile`, `sandbox/requirements.*` | Run a `stats` request in the container | 4 |
| `src/da_agent/runner/runner.py`, `service.py`, `client.py` | `DateSpan`, `StatsRun`, `StatsResult`, `Runner.stats`, `POST /v1/stats` | 4 |
| `src/da_agent/storage/sandbox_dataset.py`, `sqlite_dataset.py` | `SandboxDataset.stats`, coverage, error kind `analysis` | 5 |
| `src/da_agent/agent/analysis.py` | Tool spec, argument parsing, additivity check, guide, directive | 6 |
| `src/da_agent/agent/tools.py` | Offer `analyze`, run it, its tool result | 6 |
| `src/da_agent/agent/answer.py`, `verify.py` | `QueryRecord.analysis`, analyses as evidence | 7 |
| `src/da_agent/agent/planning.py`, `prompts.py`, `graph.py`, `src/da_agent/manifest.py` | Plan field, directive, forced kind, repeat guard, run options | 8 |
| `src/da_agent/agent/charts.py` | Charts built from analysis results | 9 |
| `src/da_agent/worker/runs.py`, `src/da_agent/api/app.py`, `eval/datamate/run.py` | Run payload options, API validation, eval flag | 10 |
| `web/src/api.ts`, `web/src/analysis.ts`, `web/src/i18n/*`, `web/src/components/chat/analysis-card.tsx`, `run-message.tsx`, `web/src/Conversation.tsx` | Card, button, drill-down chip | 11 |
| `eval/analysis/` | Synthetic shop, spec, questions, scorer, runner | 12 |
| `docs/adr/0051-fixed-analyses.md`, `docs/architecture.md`, `docs/threat-model.md`, `docs/learning/28-phan-tich-co-dinh.md`, `docs/progress.md` | Record and explain | 13 |

---

### Task 1: Pin the requests of a run without analysis

**Files:**
- Modify: `tests/unit/test_agent_graph.py`

**Interfaces:**
- Produces:
  - `StatsDataset(SqliteDataset)`, a test fake with
    `stats(self, kind, sql, x, y, *, dimensions=(), before=None, after=None, periods=1) -> StatsResult`.
    It records its calls in `self.calls` and returns `self.result`.
  - `request_hash(requests: list[LLMRequest]) -> str`: sha256 of
    `json.dumps([r.model_dump(mode="json") for r in requests], sort_keys=True, ensure_ascii=False)`.

`StatsResult` does not exist yet. Until Task 4, `stats` returns a plain dict, and Task 8 switches
it to `StatsResult`.

- [ ] **Step 1: Write the characterization test**

```python
REQUESTS_WITHOUT_ANALYSIS = "<filled in by step 2>"


def test_requests_without_analysis_are_unchanged(stats_dataset: StatsDataset) -> None:
    # The hash of every request of a plain run, taken on main before analysis existed. A run
    # with analysis off must send exactly these bytes, so eval baselines stay comparable.
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    result = run(stats_dataset, [], adapter=adapter)
    assert result.stop_reason == "answered"
    assert request_hash(adapter.requests) == REQUESTS_WITHOUT_ANALYSIS
```

`stats_dataset` is the `dataset` fixture's tables in a `StatsDataset`.

- [ ] **Step 2: Run it on unchanged code and paste the hash**

Run: `uv run pytest tests/unit/test_agent_graph.py::test_requests_without_analysis_are_unchanged -v`
Expected: FAIL showing the actual hash. Paste it into `REQUESTS_WITHOUT_ANALYSIS`, then rerun.
Expected: PASS. This must be done before any change under `src/`.

- [ ] **Step 3: Commit** (after asking the user once, see Global Constraints)

```bash
git add tests/unit/test_agent_graph.py
git commit -m "test: pin the requests of a run without analysis"
```

---

### Task 2: Port the stats module, with notes as codes

**Files:**
- Create: `src/da_agent/storage/stats.py`, ported from the archive
- Create: `tests/unit/test_stats.py`, ported, then extended
- Modify: `pyproject.toml` (the `dev` group gets the three pins), `uv.lock`

**Interfaces:**
- Produces, in `da_agent.storage.stats`:
  - **Constants:** `KINDS = ("explain_change", "trend", "forecast", "correlation", "outliers")`,
    `MAX_INPUT_ROWS`, `MAX_TABLE_ROWS`, `MAX_HORIZON`, `SIGNIFICANCE`, `OUTLIER_Z`, and
    `HISTORY_ROWS = 36`.
  - **`StatsError(ValueError)`:** `__init__(self, message: str, kind: Literal["analysis", "engine"] = "analysis")`,
    with the attribute `.kind`.
  - **`Note`:** a `TypedDict` with `code: str`, `params: dict[str, Any]`, `text: str`.
    `note(code: str, text: str, **params: Any) -> Note`.
  - **`run_stats(...) -> dict[str, Any]`**, which returns `{"kind", "summary", "table", "notes"}`:

    ```python
    run_stats(
        database: Path, kind: str, sql: str, x: str, y: str, *,
        data_start: dt.date | None, data_end: dt.date | None, periods: int = 1,
        dimensions: Sequence[str] = (),
        before: tuple[dt.date, dt.date] | None = None,
        after: tuple[dt.date, dt.date] | None = None,
    )
    ```

    `table` is `{"columns", "rows", "truncated"}` or `None`. `explain_change` dispatches to
    `da_agent.storage.change.explain_change` (Task 3), imported inside the function as the
    statsmodels imports already are.
  - **Public helpers for `change.py`:** the archive's `_numbers`, `_grain`, `_step`, `_label`,
    `_table`, `_round`, `_cell`, renamed without the underscore: `numbers`, `grain_of`, `step`,
    `label`, `table`, `rounded`, `cell`.

**Changes from the archive** (methods unchanged):
- Notes become `Note`s with these codes:
  - `unfinished_first` `{period, data_start}` and `unfinished_last` `{period, data_end}`;
  - `missing_periods` `{count, grain}` and `missing_values` `{count, column}`;
  - `interpolated` `{}`;
  - `rows_without_value` `{count}`.
  `text` keeps the archive's English sentence.
- A `duckdb.Error` in `_query` becomes `StatsError(..., kind="engine")`.
- The trend table is `[x, y, "fitted"]`, where `fitted` is the least-squares value for each
  period.
- The forecast table is `[x, y, "forecast", "low_95", "high_95"]`. It holds the last 36 history
  periods (with `y` set, forecast columns `None`), then the horizon rows (with `y` `None`).
  The table cap stays at 50 rows.

- [ ] **Step 1: Add the dev dependencies**

Run: `uv add --dev pandas==3.1.1 numpy==2.5.4 statsmodels==0.15.0`
Expected: `uv.lock` updated, and `uv run python -c "import statsmodels"` exits 0.

- [ ] **Step 2: Port the archive tests and add the new ones (failing)**

Run `git show archive/python-analysis:tests/unit/test_stats.py > tests/unit/test_stats.py`.
Update the string assertions on notes to codes. Then add:

```python
def test_notes_are_codes_with_params_and_text(db) -> None:
    result = run_stats(
        db,
        "trend",
        MONTHLY_SQL,
        "month",
        "total",
        data_start=dt.date(2026, 1, 15),
        data_end=dt.date(2026, 6, 30),
    )
    first = result["notes"][0]
    assert first["code"] == "unfinished_first"
    assert first["params"] == {"period": "2026-01", "data_start": "2026-01-15"}
    assert "unfinished" in first["text"]


def test_trend_table_has_the_fitted_line(db) -> None:
    result = run_stats(db, "trend", LINEAR_SQL, "month", "total", data_start=None, data_end=None)
    assert result["table"]["columns"] == ["month", "total", "fitted"]
    assert result["table"]["rows"][0][2] == pytest.approx(100.0)  # y = 100 + 10t exactly


def test_forecast_table_keeps_history_then_horizon(db) -> None:
    result = run_stats(
        db,
        "forecast",
        LONG_MONTHLY_SQL,
        "month",
        "total",
        data_start=None,
        data_end=None,
        periods=3,
    )
    rows = result["table"]["rows"]
    assert result["table"]["columns"] == ["month", "total", "forecast", "low_95", "high_95"]
    assert len(rows) == 36 + 3  # LONG_MONTHLY_SQL has 48 months
    assert rows[35][1] is not None and rows[35][2] is None
    assert rows[36][1] is None and rows[36][2] is not None


def test_an_engine_error_is_kind_engine(db) -> None:
    with pytest.raises(StatsError) as error:
        run_stats(db, "trend", "SELECT nope FROM missing", "a", "b", data_start=None, data_end=None)
    assert error.value.kind == "engine"
```

`db` is the archive test's fixture: a DuckDB file in `tmp_path`. Add `LINEAR_SQL` (six months,
`100 + 10t`) and `LONG_MONTHLY_SQL` (48 months) as `range()`-based SELECTs.

- [ ] **Step 3: Run to see them fail**

Run: `uv run pytest tests/unit/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: da_agent.storage.stats`.

- [ ] **Step 4: Port `stats.py` with the changes above**

Run `git show archive/python-analysis:src/da_agent/storage/stats.py` as the start.

- [ ] **Step 5: Run to see them pass**

Run: `uv run pytest tests/unit/test_stats.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/da_agent/storage/stats.py tests/unit/test_stats.py
git commit -m "feat: fixed statistical analyses computed by code"
```

---

### Task 3: `explain_change`

**Files:**
- Create: `src/da_agent/storage/change.py`
- Create: `tests/unit/test_change.py`

**Interfaces:**
- Consumes: `numbers`, `grain_of`, `step`, `label`, `rounded`, `cell`, `table`, `note`, and
  `StatsError` from Task 2.
- Produces:
  - **Constants:** `TOP_SEGMENTS = 10`, `FLAT_SHARE = 0.01`, `TOTAL = "(total)"`, `ALL = "(all)"`,
    `OTHER = "(other)"`, `BLANK = "(blank)"`, and
    `COLUMNS = ["dimension", "segment", "before", "after", "change", "share_percent", "status"]`.
  - **The function** returns `{"kind": "explain_change", "summary", "table", "notes"}`:

    ```python
    explain_change(
        frame: pd.DataFrame, x: str, y: str, dimensions: Sequence[str],
        before: tuple[dt.date, dt.date], after: tuple[dt.date, dt.date], *,
        data_start: dt.date | None, data_end: dt.date | None,
    ) -> dict[str, Any]
    ```

**Behaviour** (from the spec; this is the contract the tests pin):

- **Checks, in order, each a `StatsError` whose message names the fix:**
  1. A dimension or `x`/`y` that is not a column: "no column 'z'; the query returns [...]".
  2. `before` must end before `after` starts: "before must end before after starts".
  3. `x` holds whole dates.
  4. **Grain.** `g = grain_of(sorted distinct days)`. For `g != "day"`, each range must start on a
     period start and end on a period's last day. Otherwise: "x is one row per {g}, so each range
     must start and end on whole {g}s; group x by day to compare part of a {g}".
  5. **Coverage.** With `data_start`/`data_end` set, a range outside them is refused. One past the
     end gives: "the data covers {data_start} to {data_end}, and {start} to {end} runs past it;
     end the range on {data_end} and compare the same days of the other period". With both
     `None`, the minimum and maximum of `x` are used and the note `coverage_unknown`
     `{start, end}` is added.
  6. A range with no rows: "no rows between {start} and {end}".
- **Computation:**
  - `before_total` and `after_total` are sums of `y` over the rows whose `x` is in each range.
    `change = after_total - before_total`. `change_percent` is `None` when `before_total == 0`,
    with the note `before_zero`.
  - Segment values become strings. Blank and NULL become `(blank)`. Each segment has `status`
    `new` (no rows before), `gone` (no rows after), or `both`.
  - `flat` is `abs(change) < FLAT_SHARE * abs(before_total)`, or `change == 0` when
    `before_total == 0`. When flat, `share_percent` is `None` and the note `flat_total` is added.
    Otherwise `share_percent = 100 * change_v / change`, rounded to one decimal.
  - **Ranking.** A dimension's score is its largest `share_percent`, with ties kept in input order.
    When flat, the score is `sum(|change_v|)`. `summary["dimensions"]` is the ranked names.
  - **Table rows:**
    - first `[TOTAL, ALL, before_total, after_total, change, 100.0 or None, "both"]`;
    - then, per dimension in rank order, its segments sorted by `|change_v|` descending, at most
      10, then one `[dim, OTHER, ...sums of the rest..., status "other"]` if more remain.
  - **Summary:**
    - `method`, `grain`;
    - `before {start, end, total}` and `after {start, end, total}`, with ISO dates;
    - `change`, `change_percent`, `flat`, `dimensions`;
    - `top {dimension, segment, change, share_percent}`: the best segment of the first-ranked
      dimension, or `None` when flat.

- [ ] **Step 1: Write the failing tests**

```python
SEP, OCT = (
    (dt.date(2026, 9, 1), dt.date(2026, 9, 30)),
    (dt.date(2026, 10, 1), dt.date(2026, 10, 31)),
)


def test_the_drop_splits_by_branch_and_sums_to_the_change() -> None:
    frame = daily(  # helper: one row per day and segment, constant per segment and month
        {("Q1", "Đồ uống"): (300, 100), ("Q3", "Đồ ăn"): (200, 160), ("Q7", "Đồ ăn"): (100, 110)},
        SEP,
        OCT,
        dims=("branch", "category"),
    )
    result = explain_change(
        frame,
        "day",
        "revenue",
        ["branch", "category"],
        SEP,
        OCT,
        data_start=SEP[0],
        data_end=OCT[1],
    )
    s = result["summary"]
    assert s["change"] == pytest.approx(s["after"]["total"] - s["before"]["total"])
    rows = [r for r in result["table"]["rows"] if r[0] == "branch"]
    assert sum(r[4] for r in rows) == pytest.approx(s["change"])
    assert s["top"]["dimension"] == "branch" and s["top"]["segment"] == "Q1"
    assert result["table"]["columns"] == COLUMNS


def test_offsetting_segments_can_share_more_than_all_of_the_change() -> None:
    ...
    # Q1 −100, Q7 +40: Q1's share_percent is above 100, and Q7's is negative.


def test_a_flat_total_ranks_by_movement_and_has_no_shares() -> None:
    ...
    # +50 and −50: flat True, top None, share_percent None, note "flat_total".


def test_new_and_gone_segments() -> None: ...


def test_blank_numeric_and_accented_segments() -> None:
    ...
    # None, "", 12 (int), "Huỷ" → "(blank)", "(blank)", "12", "Huỷ"


def test_many_segments_keep_ten_and_other_and_still_sum() -> None:
    ...
    # 500 products: 10 rows + "(other)" for that dimension; the rows sum to the change


def test_before_must_end_before_after_starts() -> None: ...


def test_a_monthly_x_cannot_compare_part_of_a_month() -> None:
    ...
    # x = month starts; after = 2026-11-01..2026-11-26 → "whole months"


def test_a_range_past_the_data_is_refused_with_the_same_days_hint() -> None:
    ...
    # data_end 2026-11-20, after ends 2026-11-26 → message holds "2026-11-20" and "same days"


def test_unknown_coverage_uses_x_and_notes_it() -> None: ...


def test_a_range_without_rows_is_refused() -> None: ...


def test_a_zero_before_total_has_no_percent() -> None: ...
```

Each body follows the first test's shape: build the frame with `daily(...)`, call
`explain_change`, and assert the values in the comment.

The test helper's signature is
`daily(segments: dict[tuple, tuple[float, float]], before, after, dims) -> pd.DataFrame`. For
each segment key (a tuple of dimension values) and its `(before_per_day, after_per_day)`, it
writes one row per day of each range, with columns `day`, the dimensions, and `revenue`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_change.py -v`
Expected: FAIL with `ModuleNotFoundError: da_agent.storage.change`.

- [ ] **Step 3: Implement `explain_change` in `change.py`, and dispatch to it from `run_stats`**

`run_stats` checks that `before`, `after`, and 1–4 `dimensions` are given for this kind, and
raises `StatsError` otherwise.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/unit/test_change.py tests/unit/test_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/storage/change.py src/da_agent/storage/stats.py tests/unit/test_change.py
git commit -m "feat: explain_change splits a change between two periods by segment"
```

---

### Task 4: Run stats in the sandbox, through the Runner

**Files:**
- Modify: `src/da_agent/runner/runner.py`, `src/da_agent/runner/service.py`,
  `src/da_agent/runner/client.py`
- Modify: `sandbox/da_sandbox/__main__.py`, `sandbox/Dockerfile`, `sandbox/requirements.in`,
  `sandbox/requirements.txt`
- Test: `tests/unit/test_runner.py`, `tests/unit/test_runner_service.py`
- Create: `tests/security/test_sandbox_stats.py` (Docker)

**Interfaces:**
- Produces, in `da_agent.runner.runner`:
  - `StatsKind = Literal["explain_change", "trend", "forecast", "correlation", "outliers"]`.
  - `DateSpan(BaseModel)`, frozen, `extra="forbid"`, with `start: dt.date` and `end: dt.date`,
    and a validator that `start <= end`.
  - `StatsRun(BaseModel)`, frozen, `extra="forbid"`:
    - `kind: Literal["stats"] = "stats"`, `dataset_id` (same pattern as `SqlRun`),
      `analysis: StatsKind`;
    - `sql` (max `MAX_SQL_CHARS`), `x` and `y` (max 200 each), `dimensions: list[str]` (max 4
      items, each max 200);
    - `before` and `after: DateSpan | None = None`, `periods: int = Field(1, ge=1, le=12)`;
    - `data_start` and `data_end: dt.date | None = None`,
      `timeout_seconds: int = Field(30, ge=1, le=60)`.
  - `StatsResult(BaseModel)`:
    - `status: RunStatus`, `kind: str = ""`;
    - `summary: dict[str, Any] = {}`, `table: dict[str, Any] | None = None`,
      `notes: list[dict[str, Any]] = []`;
    - `error: str | None = None`, `error_kind: Literal["analysis", "engine"] | None = None`,
      `duration_seconds: float = 0.0`.
  - `SandboxRunner.stats(self, request: StatsRun) -> StatsResult`, and `Runner.stats`, which
    mounts the dataset like `SqlRun`. Its messages:
    - timeout: "analysis stopped: over the {n} s time limit";
    - killed: "analysis stopped: over the memory limit; aggregate the query more".
    `Runner.run` accepts `StatsRun` and sends `request.model_dump_json()`, because dates do not go
    through `json.dumps`.
- Produces, in the service: `POST /v1/stats`, behind the same token. Produces, in the client:
  `RunnerClient.stats(request: StatsRun) -> StatsResult`, which returns an error result when the
  service is unavailable, as `query` does.
- **Sandbox:** `run_stats(request: dict) -> int` turns `before` and `after` into
  `(start, end)` date tuples and `data_start`/`data_end` into dates. It prints the `run_stats`
  result as JSON. On a `StatsError` it prints `{"error": str, "error_kind": kind}`. `main()`
  dispatches `kind == "stats"`.

- [ ] **Step 1: Write the failing unit tests**

In `test_runner.py`, using the existing fake `execute`:
- `test_stats_runs_in_a_fresh_sandbox_with_the_dataset_read_only`: the command is `SqlRun`'s,
  with `--network=none`, the dataset mounted at `/data:ro`, and the query memory limit.
- `test_stats_sends_dates_as_json`: stdin holds `"before": {"start": "2026-09-01", ...}`.
- `test_a_stats_error_keeps_its_kind`: stdout
  `{"error": "no rows between ...", "error_kind": "analysis"}` gives `status == "error"`,
  `error_kind == "analysis"`, and the message is kept.
- `test_stats_timeout_and_kill_have_messages`.

In `test_runner_service.py`:
- `test_stats_needs_the_token`;
- `test_stats_passes_through`;
- `test_the_client_reports_an_unavailable_service_for_stats`.

In a new test file or in the existing `test_runner.py`:
- `test_date_span_must_not_end_before_it_starts`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_runner.py tests/unit/test_runner_service.py -v`
Expected: FAIL with `ImportError: StatsRun`.

- [ ] **Step 3: Implement the models, `Runner.stats`, the route, the client method, and the
  sandbox `run_stats`**

Start from the archive's diff of these files, without `AnalysisRun`.

- [ ] **Step 4: Add the libraries to the image**

1. Append `pandas==3.1.1`, `numpy==2.5.4`, `statsmodels==0.15.0` to `sandbox/requirements.in`,
   then recompile with the header command.
2. Add `src/da_agent/storage/stats.py` and `change.py` to the `COPY` of storage files in
   `sandbox/Dockerfile`.

- [ ] **Step 5: Write the Docker tests**

`tests/security/test_sandbox_stats.py` follows `test_sandbox_sql.py`: two datasets, one holding a
canary, plus `answers.jsonl` in the root. Tests:
- `test_explain_change_runs_on_its_own_dataset`: a real `StatsRun` returns a table whose branch
  rows sum to the change.
- `test_stats_sql_cannot_read_other_datasets_or_files`: three SQL inputs, each giving
  `status == "error"` with no canary in the result:
  `read_csv('/data/../shop_2/dataset.duckdb')`, `read_text('/proc/self/environ')`, and
  `ATTACH ...`.
- `test_a_million_rows_fit_in_the_query_memory_limit`: 1,000,000 rows of `(day, branch, amount)`
  give `explain_change` `status == "ok"` with `memory_mb=512`. **If it is killed**, add
  `stats_memory_mb: int = 1024` to `RunnerConfig`, use it for `StatsRun` in `build_command`, and
  note it in the ADR (Task 13). Do not lower `MAX_INPUT_ROWS`.

- [ ] **Step 6: Run all of it**

Run: `uv run pytest tests/unit/test_runner.py tests/unit/test_runner_service.py tests/security/test_sandbox_stats.py -v`
Expected: PASS. The Docker tests run, not skip, on a machine with Docker. Record the sandbox image
size (`docker image ls da-agent-sandbox:dev`) for the ADR.

- [ ] **Step 7: Commit**

```bash
git add src/da_agent/runner sandbox tests/unit/test_runner.py tests/unit/test_runner_service.py tests/security/test_sandbox_stats.py
git commit -m "feat: stats requests run in the sandbox through the Runner"
```

---

### Task 5: `SandboxDataset.stats`

**Files:**
- Modify: `src/da_agent/storage/sandbox_dataset.py`, `src/da_agent/storage/sqlite_dataset.py`
- Test: `tests/unit/test_sandbox_dataset.py`

**Interfaces:**
- Consumes: `StatsRun`, `StatsResult`, `DateSpan`, `StatsKind` (Task 4).
- Produces:
  - `QueryErrorKind` gains `"analysis"`.
  - `QueryRunner` (the protocol in `sandbox_dataset.py`) gains
    `stats(self, request: StatsRun) -> StatsResult`.
  - The method:

    ```python
    SandboxDataset.stats(
        self, kind: StatsKind, sql: str, x: str, y: str, *,
        dimensions: Sequence[str] = (), before: DateSpan | None = None,
        after: DateSpan | None = None, periods: int = 1,
    ) -> StatsResult
    ```

    It guards the SQL with `validate_select` (a rejection raises `QueryError(..., "rejected")`)
    and fills `data_start`/`data_end` from `_coverage(checked, x)`. It maps a non-ok status to a
    `QueryError`: `timeout` → `"timeout"`, `killed` → `"limit"`, `error` → `result.error_kind`
    or `"engine"`.
  - `_coverage(self, sql: str, x: str) -> tuple[dt.date | None, dt.date | None]`:
    - First, the column `x` comes from. In the outer SELECT, the projection whose alias or name is
      `x` is found, and its first `exp.Column` names a date column of a table the query reads.
      That column's `value_range` is used.
    - Else, the archive's `_date_range`: the overlap of the date columns of the tables read.
    - Else `(None, None)`.

- [ ] **Step 1: Write the failing tests** with a fake runner that records the `StatsRun`:
  - `test_stats_guards_the_sql`: `DELETE FROM orders` raises `QueryError` with kind `rejected`,
    and the runner is not called.
  - `test_coverage_comes_from_the_column_x_is_made_from`: in
    `SELECT CAST(order_date AS DATE) AS day ...`, `data_start`/`data_end` are `order_date`'s
    range, not the calendar table's.
  - `test_coverage_falls_back_to_the_tables_read`: in `SELECT 1 AS day ...`, the overlap of the
    tables read is used.
  - `test_an_analysis_error_has_kind_analysis` and `test_a_stats_timeout_is_kind_timeout`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_sandbox_dataset.py -v`
Expected: FAIL with `AttributeError: 'SandboxDataset' object has no attribute 'stats'`.

- [ ] **Step 3: Implement it**

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/unit/test_sandbox_dataset.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/storage/sandbox_dataset.py src/da_agent/storage/sqlite_dataset.py tests/unit/test_sandbox_dataset.py
git commit -m "feat: sandbox datasets run fixed analyses with their date coverage"
```

---

### Task 6: The `analyze` tool

**Files:**
- Create: `src/da_agent/agent/analysis.py`
- Modify: `src/da_agent/agent/tools.py`
- Test: `tests/unit/test_analysis_tool.py`

**Interfaces:**
- Consumes: `StatsKind`, `DateSpan`, `StatsResult` (Task 4); `SandboxDataset.stats` (Task 5),
  used duck-typed.
- Produces, in `da_agent.agent.analysis`:
  - **Constants:** `ANALYZE = "analyze"`, `ANALYZE_KINDS = typing.get_args(StatsKind)`,
    `PLAN_ANALYSES = ("none", *ANALYZE_KINDS)`, `MAX_ANALYSES = 4`, `MAX_DIMENSIONS = 4`,
    `MAX_HORIZON = 12`.
  - **Copy:** `ANALYZE_SPEC: ToolSpec`, `ANALYSIS_GUIDE: str`, and
    `CHANGE_GUIDANCE = "These are where the change happened, not why it happened; say so in the answer."`
  - **`AnalyzeRequest`**, a frozen dataclass: `kind: StatsKind`, `sql: str`, `x: str`, `y: str`,
    `dimensions: tuple[str, ...] = ()`, `before: DateSpan | None = None`,
    `after: DateSpan | None = None`, `periods: int = 1`.
  - **Functions:**
    - `class AnalyzeArgumentError(ValueError)`;
    - `parse_analyze(arguments: dict[str, Any]) -> AnalyzeRequest`, which raises
      `AnalyzeArgumentError`;
    - `request_json(request: AnalyzeRequest) -> dict[str, Any]`: the normalized arguments, with
      ISO dates;
    - `additive_problem(sql: str, y: str, dialect: str = "duckdb") -> str | None`;
    - `analysis_directive(kind: str) -> str`, which is `""` for `"none"`;
    - `can_stats(dataset: object) -> bool`, which is `callable(getattr(dataset, "stats", None))`.
- Produces, in `da_agent.agent.tools`:
  - `tool_specs(rules, dialect="SQLite", charts=False, analysis=False)` appends `ANALYZE_SPEC`
    when `analysis` is true.
  - `execute_tool(call, dataset, rules=None, *, charts_left=None, analyses_left=None)`.
    `analyses_left` is `None` when the run does not offer `analyze`.
  - The successful tool content is JSON
    `{"kind", "arguments": request_json(...), "summary", "table", "notes"}`, plus
    `"guidance": CHANGE_GUIDANCE` for `explain_change`. The step is
    `ToolStep(tool=ANALYZE, ok=True, sql=request.sql, row_count=len(table rows) or 1)`.

**Exact copy.** The tool spec is model-facing text, and the implementer cannot guess it:

```python
ANALYZE_SPEC = ToolSpec(
    name=ANALYZE,
    description=(
        "Run a fixed analysis on one query's result. Code picks the method and reads every row "
        "the query returns, so aggregate to the level you want first. Kinds: "
        "'explain_change': where did a sum or count change between two periods? x is a date, y "
        "a SUM or COUNT, and dimensions are 1 to 4 columns that could explain the change, such "
        "as branch, channel, or category. before and after are date ranges from the periods "
        "given with the question; group x by day to compare part of a month. "
        "'trend': is y rising or falling? x is one date per period, for example "
        "date_trunc('month', order_date). 'forecast': the next periods of y with a 95% "
        "interval; x as for trend. 'correlation': do two numeric columns x and y move "
        "together? 'outliers': which rows have an unusual y? x labels each row, such as a day. "
        "Unfinished first or last periods are left out for you. Answer with the numbers it "
        "returns."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(ANALYZE_KINDS)},
            "sql": {
                "type": "string",
                "description": "One SELECT that returns x, y, and any dimensions.",
            },
            "x": {"type": "string", "description": "The column of dates, labels, or values."},
            "y": {"type": "string", "description": "The numeric column to analyse."},
            "dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_DIMENSIONS,
                "description": "explain_change only: columns of the query to split by.",
            },
            "before": {
                "type": "object",
                "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                "description": "explain_change only: the earlier range, ISO dates, both ends included.",
            },
            "after": {
                "type": "object",
                "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                "description": "explain_change only: the later range.",
            },
            "periods": {
                "type": "integer",
                "description": f"forecast only: how many periods ahead, 1 to {MAX_HORIZON}.",
            },
        },
        "required": ["kind", "sql", "x", "y"],
    },
)

ANALYSIS_GUIDE = (
    "When to use analyze: to explain where a total changed between two periods "
    "(explain_change), for a trend or growth, a forecast, a correlation, or outliers, call "
    "analyze and answer with its numbers. Do not judge these by reading rows through run_sql: "
    "its results are cut to a few dozen rows. For a single total, count, or ranking, run_sql "
    "is enough."
)
```

`analysis_directive` text:
- **For `explain_change`:** "This question needs an explain_change analysis: call analyze with
  kind 'explain_change', a query that returns one row per day and segment with a SUM or COUNT as
  y, the columns that could explain the change as dimensions, and before and after ranges from
  the periods above. Answer with the numbers it returns, and say that they show where the change
  happened, not why. Do not compute it in run_sql."
- **For other kinds** (the archive's text): f"This question needs a {kind} analysis: call analyze
  with kind '{kind}' and a query that returns one row per period or item, then answer with the
  numbers it returns. Do not compute it in run_sql."

**`additive_problem` rule.** Parse with sqlglot. Find the outer SELECT's projection whose alias or
name is `y`. If there is none, return `None` (the sandbox names the missing column).
- **Allowed:** after unwrapping `Alias`, `Cast`, and `Coalesce`, the expression is `exp.Sum`, or
  `exp.Count` without `Distinct`, or it contains no aggregate at all (code sums it).
- **Refused,** with "explain_change splits sums and counts, and y is {expression} here; use SUM
  or COUNT, or answer this one with run_sql": anything else, such as `AVG`, `MIN`, `MAX`,
  `COUNT(DISTINCT ...)`, or arithmetic across aggregates.

**`_analyze` order** (the error kind of each refusal in brackets):
1. parse the arguments [`arguments`];
2. `analyses_left <= 0` [`analysis`, "a run runs at most 4 analyses"];
3. for `explain_change`, `additive_problem` [`analysis`];
4. `dataset.stats(...)`; a `QueryError` keeps its kind.

A crash keeps the SQL, as for `run_sql`: add `ANALYZE` to the tuple in the `except` branch of
`execute_tool`.

**Span.** On the current span (`tool.analyze`, opened by `tools_node`), `_analyze` sets
`da_agent.analysis.kind`. On success it also sets `da_agent.analysis.rows` (the table rows) and
`da_agent.analysis.seconds` (`StatsResult.duration_seconds`).

- [ ] **Step 1: Write the failing tests**, using a fake dataset with a `stats` method:
  - `test_analyze_is_offered_only_when_asked`: the spec names are compared, with and without
    `analysis=True`.
  - `test_parse_analyze_needs_ranges_and_dimensions_for_explain_change`, plus a parametrized test
    over bad input: an unknown kind, a non-ISO date, five dimensions, `periods` of 0, 13, or
    `True`. Each gives `AnalyzeArgumentError`.
  - `test_additive_problem`, parametrized:
    - `None` for `SUM(amount) AS revenue`, `COUNT(*) AS orders`, `quantity * price AS amount`,
      `CAST(SUM(a) AS DOUBLE) AS r`;
    - a message holding "sums and counts" for `AVG(amount) AS aov`,
      `COUNT(DISTINCT id) AS orders`, `SUM(a) / COUNT(*) AS aov`.
  - `test_a_successful_explain_change_returns_its_result_and_guidance`: the tool content has
    `kind`, `arguments` with ISO dates, `summary`, `table`, `notes`, and `guidance`; the step is
    ok with its SQL.
  - `test_the_fifth_analysis_is_refused`: `analyses_left=0` gives error kind `analysis`.
  - `test_a_not_additive_y_never_reaches_the_sandbox`: the fake records no call.
  - `test_analyze_unknown_without_the_offer`: with `analyses_left=None`, the result is error kind
    `unknown_tool`.
  - `test_analyze_records_its_kind_and_time_on_the_span`, with the in-memory span exporter that
    `tests/unit/test_tracing_and_factory.py` uses.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_analysis_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: da_agent.agent.analysis`.

- [ ] **Step 3: Implement `analysis.py` and the `tools.py` changes**

- [ ] **Step 4: Run to see them pass, then run the suite**

Run: `uv run pytest tests/unit/test_analysis_tool.py tests/unit/test_agent_graph.py -v`
Expected: PASS. The Task 1 hash test still passes.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/agent/analysis.py src/da_agent/agent/tools.py tests/unit/test_analysis_tool.py
git commit -m "feat: analyze tool with argument and additivity checks"
```

---

### Task 7: Analyses are evidence

**Files:**
- Modify: `src/da_agent/agent/answer.py`, `src/da_agent/agent/verify.py`
- Create: `tests/unit/test_verify_analysis.py`

**Interfaces:**
- Consumes: `ANALYZE` (Task 6); the tool content shape (Task 6).
- Produces:
  - `QueryRecord.analysis: dict[str, Any] | None = None`. For an analysis it holds
    `{"kind", "arguments", "summary", "notes"}`. Its `columns` and `rows` are the analysis table:
    empty for `correlation`, `truncated` from the table.
  - `AgentResult.analyses` is a property returning `[q for q in self.queries if q.analysis]`.
  - `verify.record_numbers(record: QueryRecord) -> list[float]`: the numbers in `rows`, plus the
    numbers in `analysis["summary"]` when it is set.
  - `verify.query_records(messages)` also returns a record for each successful `analyze` call, in
    call order with the queries. The SQL is the call's `sql`.
  - `verify.result_numbers(messages)` includes every analysis's summary and table numbers.
  - `evidence_indices` and `_query_results` use `record_numbers`.
  - The `ToolStep.error_kind` comment lists `"analysis"` and `"repeat"`.

- [ ] **Step 1: Write the failing tests**, building `messages` by hand with one `analyze` call
  and its tool reply:
  - `test_an_analysis_is_a_query_record_with_its_table_and_summary`.
  - `test_a_number_from_an_analysis_summary_traces`: the answer value equals
    `summary["change"]`, and `answer_problems(...)` finds no `trace` problem.
  - `test_the_analysis_is_the_answers_evidence`: `evidence_queries` returns the analysis SQL.
  - `test_the_period_check_reads_analysis_sql`: an analysis SQL filtered to 2030 on a 2026
    dataset gives the period problem.
  - `test_a_failed_analysis_is_not_evidence`: a tool reply with `{"error": ...}` gives no record.
  - `test_old_results_without_analysis_load`: `result_from_json` on a dict whose queries lack
    `analysis` gives records with `analysis is None` and `AgentResult.analyses == []`.
  - `test_report_and_explanation_cover_an_analysis`, on an `AgentResult` whose evidence is an
    analysis:
    - `explain_result(result, labels)` has an entry with that `query_index`;
    - the HTML report holds the analysis SQL;
    - a number from the analysis table links to its cell.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_verify_analysis.py -v`
Expected: FAIL on the first assertion that needs an analysis record.

- [ ] **Step 3: Implement it**

- [ ] **Step 4: Run them and the verify suite**

Run: `uv run pytest tests/unit/test_verify_analysis.py tests/unit/test_verify_periods.py tests/unit/test_verify_fanout.py tests/unit/test_verify_inputs.py tests/unit/test_report.py tests/unit/test_explain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/agent/answer.py src/da_agent/agent/verify.py tests/unit/test_verify_analysis.py
git commit -m "feat: analyses are evidence for verification, reports, and explanations"
```

---

### Task 8: Plan field, forced kind, and the graph

**Files:**
- Modify: `src/da_agent/agent/planning.py`, `src/da_agent/agent/prompts.py`,
  `src/da_agent/agent/graph.py`, `src/da_agent/manifest.py`
- Test: `tests/unit/test_agent_graph.py`, `tests/unit/test_manifest.py`

**Interfaces:**
- Consumes: Tasks 6 and 7.
- Produces:
  - **`planning.plan_spec(analysis: bool) -> ToolSpec`:** `PLAN_SPEC` when false. When true, a
    copy with a required `analysis` enum `PLAN_ANALYSES`, described as: "The analysis the question
    needs: explain_change (where a total changed between two periods, or why it rose or fell),
    trend (rising or falling over time), forecast (future periods), correlation (two measures
    moving together), outliers (unusual rows), or none (totals, counts, rankings, lookups)."
  - **`Plan.analysis: str = "none"`:** `parse_plan` keeps it only when it is in `PLAN_ANALYSES`.
  - **`plan_system_prompt(documents, interactive, analysis: bool = False)`:** appends
    `ANALYSIS_GUIDE + " Name the analyze step in the plan."` when true.
  - **`plan_notes(steps, clarifications, analysis: str = "none")`:** puts
    `analysis_directive(analysis)` first.
  - **`build_system_prompt(documents, *, schema_given=False, analysis=False)`:** appends
    `ANALYSIS_GUIDE` when true.
  - **Graph state and context:** `AgentContext.analysis: bool = False` and
    `AgentContext.analysis_kind: str | None = None`. `AgentState.plan_analysis: str`, owned by
    `plan_node`.
  - **`run_graph(..., analysis: bool = False, analysis_kind: str | None = None)`:** offers
    `analyze` when `analysis and can_stats(dataset)`. `analysis_kind` is used only when it is
    offered.
  - **`plan_node`:** sets `plan_analysis = context.analysis_kind or (plan.analysis if offered else "none")`
    on every return path, including a failed or garbled plan.
  - **`tools_node`:** passes `analyses_left=_analyses_left(context, state, steps)`, which is
    `MAX_ANALYSES` minus the successful `analyze` steps so far. It runs the repeat guard for
    `ANALYZE` only. Port `_call_key`, `_finished_calls`, `_repeat`, and `REPEAT_NOTICE` from the
    archive; their error kind is `repeat`.
  - **`_charts_left`:** counts only `make_chart` steps.
  - **`verify_node`:** `queries_ok` counts successful `run_sql` and `analyze` steps.
  - **`node_events`:** the plan event carries `"analysis"` when it is not `"none"`. The plan span
    gets `da_agent.plan.analysis`.
  - **`manifest`:**
    - `AGENT_PROMPT_MODULES` adds `analysis`;
    - `agent_prompt_samples()` adds `plan_system_prompt([], True, analysis=True)`,
      `build_system_prompt([], schema_given=True, analysis=True)`, and `analysis_directive(k)`
      for each kind in `ANALYZE_KINDS`.
  - **The test fake:** `StatsDataset.stats` (Task 1) now returns a `StatsResult`.

- [ ] **Step 1: Write the failing graph tests**. Extend `run(...)` with `analysis` and
  `analysis_kind` keyword arguments, and use `stats_dataset`.
  - `test_with_analysis_the_plan_must_name_one`: the plan request's tool has a required
    `analysis`, and the first agent system prompt holds `ANALYSIS_GUIDE`.
  - `test_the_named_analysis_becomes_a_directive`: the plan says `trend`, so the first agent user
    message starts with `analysis_directive("trend")`.
  - `test_a_forced_kind_overrides_the_plan`: `analysis_kind="explain_change"` with the plan
    saying `none` gives the directive for `explain_change`.
  - `test_an_analysis_answer_is_verified_and_recorded`: script
    `plan(analysis) → call("analyze", {...}) → submit("number", summary change)`. The result is
    `answered`, `result.analyses[0].analysis["kind"] == "explain_change"`, and the run has no
    `run_sql` step.
  - `test_a_refused_explain_change_still_lets_the_run_answer_from_sql`: the fake raises
    `QueryError("...sums and counts...", "analysis")`, then the script goes
    `sql() → submit("number", 350)`. The result is `answered`.
  - `test_a_repeated_analysis_is_answered_from_the_conversation`: the same `analyze` call twice
    gives one `stats` call, and the second step has `error_kind == "repeat"`.
  - `test_analysis_charts_do_not_use_up_make_chart`.
  - `test_analysis_on_a_dataset_without_stats_is_not_offered`: on `SqliteDataset` with
    `analysis=True`, the requests carry no `analyze` tool.
  - `test_a_checkpoint_without_plan_analysis_resumes`: save state without `plan_analysis`
    (`InMemorySaver`), resume, and the run answers.

  In `test_manifest.py`, add `test_the_analysis_copy_is_in_the_fingerprint`: changing
  `analysis.ANALYSIS_GUIDE` (by monkeypatch) changes the agent fingerprint.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_agent_graph.py tests/unit/test_manifest.py -v`
Expected: the new tests FAIL (`run_graph() got an unexpected keyword argument 'analysis'`). The
Task 1 hash test passes.

- [ ] **Step 3: Implement it**

- [ ] **Step 4: Run to see them pass, including the hash test**

Run: `uv run pytest tests/unit -v`
Expected: PASS, and `test_requests_without_analysis_are_unchanged` passes unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/agent src/da_agent/manifest.py tests/unit/test_agent_graph.py tests/unit/test_manifest.py
git commit -m "feat: the plan names an analysis and the graph offers analyze"
```

---

### Task 9: Charts from analyses

**Files:**
- Modify: `src/da_agent/agent/charts.py`, `src/da_agent/agent/graph.py` (`_result`)
- Test: `tests/unit/test_charts.py`

**Interfaces:**
- Consumes: `QueryRecord.analysis` (Task 7).
- Produces:
  - `analysis_chart(record: QueryRecord) -> dict[str, Any] | None`. It returns a Vega-Lite spec
    for `explain_change` (a waterfall), `trend` (`y` as a line plus `fitted` as a second line),
    and `forecast` (a history line, a forecast line, and a band between `low_95` and `high_95`).
    It returns `None` for `correlation` and `outliers`.
  - `with_analysis_charts(records: list[QueryRecord]) -> list[QueryRecord]`: records with an
    analysis get `analysis["chart"]`.
  - `_result` in `graph.py` applies it to `query_records(...)`.

The waterfall uses the table's `(total)` row and the first-ranked dimension's rows. Bars go from
the before total, through each segment's change (including `(other)`), to the after total. Data
values come from the table, and the title is `y`.

- [ ] **Step 1: Write the failing tests:**
  - `test_explain_change_waterfall_starts_and_ends_at_the_totals`: the first and last data values
    are the before and after totals, and one bar per segment of the top dimension sits between
    them.
  - `test_trend_chart_has_the_series_and_the_fitted_line`.
  - `test_forecast_chart_has_history_forecast_and_band`.
  - `test_no_chart_for_correlation_or_outliers`.
  - `test_analysis_charts_hold_no_model_text`: no field of the spec comes from the model's
    arguments except column names.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_charts.py -v`
Expected: FAIL with `ImportError: analysis_chart`.

- [ ] **Step 3: Implement it**

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/unit/test_charts.py tests/unit/test_agent_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/agent/charts.py src/da_agent/agent/graph.py tests/unit/test_charts.py
git commit -m "feat: charts built by code from analysis results"
```

---

### Task 10: Worker, API, and the eval flag

**Files:**
- Modify: `src/da_agent/worker/runs.py`, `src/da_agent/api/app.py`, `eval/datamate/run.py`
- Test: `tests/integration/test_api.py`, `tests/integration/test_worker.py`,
  `tests/unit/test_datamate_eval.py`

**Interfaces:**
- Consumes: `run_graph(..., analysis, analysis_kind)` (Task 8); `StatsKind` (Task 4).
- Produces:
  - **API:** `RunRequest.analysis: bool = True` and `RunRequest.analysis_kind: StatsKind | None = None`.
    A `model_validator` rejects `analysis_kind` with `analysis=False`, which is a 422.
    `create_run` writes `"analysis": body.analysis` and, when set, `"analysis_kind"` into the
    payload.
  - **Worker:** `_graph_options(payload: dict[str, Any]) -> dict[str, Any]` returns
    `{"charts": bool(payload.get("charts", True)), "analysis": bool(payload.get("analysis", False)), "analysis_kind": payload.get("analysis_kind")}`.
    It is used by `fork`, `rerun`, and `__call__`. The `analysis` default is `False`, so jobs
    queued before this change and golden reruns stay plain runs.
  - **Eval:** `eval/datamate/run.py --analysis` passes `analysis=True` to `run_graph`. Argparse
    errors when it is given without `--agent graph`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_run_payload_asks_for_analysis`: `create_run` stores `analysis: true`.
  - `test_a_forced_kind_is_stored`.
  - `test_a_forced_kind_without_analysis_is_422`.
  - `test_an_unknown_kind_is_422`.
  - Worker, with a fake `run_graph` capture as the existing worker tests use:
    `test_the_worker_passes_analysis_options` and
    `test_a_job_queued_before_analysis_runs_without_it`. The second uses a payload with no
    `analysis` key and expects `analysis=False`.
  - Eval: `test_analysis_flag_needs_the_graph_agent`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run --env-file .env pytest tests/integration/test_api.py tests/integration/test_worker.py tests/unit/test_datamate_eval.py -v -k analysis`
Expected: FAIL.

- [ ] **Step 3: Implement it**

- [ ] **Step 4: Run to see them pass**

Run: the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/worker/runs.py src/da_agent/api/app.py eval/datamate/run.py tests
git commit -m "feat: product runs offer analysis; a run can force its kind"
```

---

### Task 11: Web: analysis card, "Why did it change?", drill-down

**Files:**
- Modify: `web/src/api.ts`, `web/src/i18n/en.ts`, `web/src/i18n/vi.ts`,
  `web/src/components/chat/run-message.tsx`, `web/src/Conversation.tsx`
- Create: `web/src/analysis.ts`, `web/src/analysis.test.ts`,
  `web/src/components/chat/analysis-card.tsx`

**Interfaces:**
- Consumes: the run JSON. `result.queries[i].analysis` holds `{kind, arguments, summary, notes, chart?}`.
  `run.explanations[*].query_index` gives the evidence indices.
- Produces:
  - **In `api.ts`:**
    - `type AnalysisKind = "explain_change" | "trend" | "forecast" | "correlation" | "outliers"`;
    - `interface AnalysisNote { code: string; params: Record<string, unknown>; text: string }`;
    - `interface AnalysisInfo { kind; arguments; summary; notes; chart? }`;
    - `QueryRecord.analysis?: AnalysisInfo | null`;
    - `createRun(datasetId, question, asOf?, conversationId?, analysisKind?: AnalysisKind)`,
      which sends `analysis_kind` when given.
  - **In `analysis.ts`:**
    - `interface FollowUp { question: string; kind: AnalysisKind }`;
    - `evidenceAnalyses(run: Run): { index: number; record: QueryRecord; info: AnalysisInfo }[]`,
      from the explanation indices;
    - `changeSegments(record: QueryRecord): { dimension: string; segments: unknown[][] }[]`, in
      table order, without the total row;
    - `analysisHeadline(record: QueryRecord, m: Messages): string`;
    - `noteText(note: AnalysisNote, m: Messages): string`: translated by `code`, falling back to
      `note.text`;
    - `whyChangedFollowUp(run: Run, m: Messages): FollowUp | null`;
    - `drillFollowUp(record: QueryRecord, m: Messages): FollowUp | null`.
  - **Components:** `AnalysisCard({ record, info, onFollowUp })`. `RunMessage` gets an
    `onFollowUp?: (followUp: FollowUp) => void` prop. `Conversation` passes one that calls
    `askInConversation(text, kind)`.

**Rules for the follow-ups** (pinned by tests):
- **`whyChangedFollowUp`** returns non-null only when all of these hold:
  - the run succeeded with an answer;
  - no evidence analysis is already an `explain_change`;
  - either a `make_chart` step's spec has a temporal x with at least 2 data values, or an evidence
    query's first column holds ISO dates and the query has at least 2 rows.
  Its question is `m.analysis.whyQuestion(measure, previousLabel, lastLabel)`, where `measure` is
  the chart's y field or the query's first numeric column name.
- **`drillFollowUp`** returns non-null for an `explain_change` with `summary.top` set and at least
  2 dimensions. Its question is
  `m.analysis.drillQuestion(measure, top.segment, top.dimension, before, after, nextDimension)`,
  where `nextDimension` is the next ranked dimension. Its kind is `explain_change`.

**Copy** (en / vi). The implementer writes the remaining keys in the same voice:

| Key | English | Vietnamese |
|---|---|---|
| `analysis.whyChanged` | Why did it change? | Vì sao thay đổi? |
| `analysis.whyQuestion(m, a, b)` | `Why did ${m} change from ${a} to ${b}?` | `Vì sao ${m} thay đổi từ ${a} sang ${b}?` |
| `analysis.drill(s, d)` | `See ${s} by ${d}` | `Xem ${s} theo ${d}` |
| `analysis.drillQuestion(m, s, d, a, b, n)` | `From ${a} to ${b}, where did ${m} of ${d} ${s} change, by ${n}?` | `Từ ${a} sang ${b}, ${m} của ${d} ${s} thay đổi ở ${n} nào?` |
| `analysis.whereChanged` | Where it changed | Phần thay đổi tập trung ở |
| `analysis.notCause` | This is where it changed, not why. The data does not say why. | Đây là chỗ thay đổi, không phải lý do. Dữ liệu không cho biết lý do. |
| `analysis.flat` | The total barely moved; segments offset each other. | Tổng gần như không đổi; các nhóm bù trừ nhau. |
| `analysis.method` | How it was computed | Cách tính |
| `analysis.other` / `analysis.blank` | Other / (blank) | Khác / (trống) |
| `analysis.notes.<code>` | one entry per note code from Tasks 2 and 3 | |

**`analysisHeadline` for `explain_change`:** `${y} ${after}: ${afterTotal}, ${up|down} ${|change|} (${percent}) vs ${before}.`,
in the Vietnamese order of the spec sketch. Numbers use `Intl.NumberFormat(m.locale)`. Other kinds:
- trend: direction and slope per period;
- forecast: the next period with its 95% range;
- correlation: strength and sign;
- outliers: the count.

- [ ] **Step 1: Write the failing tests in `analysis.test.ts`** with run fixtures in the test
  file:
  - `evidenceAnalyses picks only evidence records with an analysis`;
  - `the why button needs a time series and no explain_change yet`;
  - `the why question names the last two periods`;
  - `the drill chip needs a top segment and a second dimension`;
  - `headlines in English and Vietnamese`, using `messagesFor("en")` and `messagesFor("vi")`;
  - `every note code has a translation in both languages`, iterating the codes of Tasks 2 and 3;
  - `segments group by dimension and skip the total`.

- [ ] **Step 2: Run to see them fail**

Run: `cd web && npm test -- analysis`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the types, `analysis.ts`, and i18n until the tests pass**

- [ ] **Step 4: Build the card and wire it**

`AnalysisCard` shows, in order:
- the headline;
- `<Chart spec={info.chart}>` when set, lazily loaded as in `run-message.tsx`;
- `whereChanged` with the top dimension's segments (others in a `<details>`);
- translated notes;
- `notCause` for `explain_change`;
- `method` with `summary.method`;
- the drill chip button.

`RunMessage` renders a card per evidence analysis after `AnswerValue`, and a "Why did it change?"
button in the action row when `whyChangedFollowUp(run, m)` is non-null.

- [ ] **Step 5: Run the web checks**

Run: `cd web && npm test && npm run typecheck && npm run lint`
Expected: all pass.

- [ ] **Step 6: Check it in the running app** with the `run` skill. Start the stack and upload
  `examples/demo-shop`. Then:
  - ask "Doanh thu theo tháng" and click "Vì sao thay đổi?";
  - check the card, the waterfall, the Vietnamese and English copy, and the chip;
  - take screenshots at 1440 and 390 px in light and dark.

  This step makes real Groq calls, about $0.01. Ask the user first.

- [ ] **Step 7: Commit**

```bash
git add web/src
git commit -m "feat: analysis card, why-did-it-change button, and drill-down in the web"
```

---

### Task 12: Synthetic eval with planted effects

**Files:**
- Create: `eval/analysis/generate.py`, `eval/analysis/spec.json`,
  `eval/analysis/questions.jsonl` (written by the generator), `eval/analysis/score.py`,
  `eval/analysis/run.py`, `eval/analysis/README.md`
- Create: `tests/unit/test_analysis_eval.py`
- Generated, committed: `eval/analysis/shop/orders.csv`

**Interfaces:**
- **`generate.py`**, seeded (`SEED = 20261126`), writes `shop/orders.csv` and `questions.jsonl`:
  - **Data:** orders from 2024-06-01 to 2026-11-26 (`AS_OF = "2026-11-26"`), with branches
    Q1/Q3/Q7, 4 categories, and 3 channels. Columns: `order_id`, `order_date`, `branch`,
    `category`, `channel`, `quantity`, `unit_price`, `discount_percent`, `status`
    (completed/cancelled), and `amount`.
  - **Planted effects:**
    - October 2026 revenue is −20% against September, with about 70% of the drop from Q1;
    - November 1–26 against October 1–26: a drop led by one channel;
    - one category rises steadily;
    - a December peak each year;
    - three days at ×5 revenue;
    - `discount_percent` moves with `quantity`.
  - **Questions:** about 16, mostly Vietnamese. There are 10 analysis questions: 2 per kind,
    including the same-days question and a Q1 drill-down. There are 6 controls. Each line is
    `{"id", "question", "as_of", "analysis": kind | "none", "checks": [...]}`, with values
    computed from the generated rows.
- **Check types** (`score.py`):
  - `top_segment {dimension, segment}`: read from the evidence analysis summary;
  - `ranges {before, after}`: the `explain_change` arguments;
  - `direction {value}`;
  - `number {value, tolerance}`: the answer value, relative tolerance;
  - `within {low, high}`;
  - `contains {values}`: the outliers table's x;
  - `correlation {sign, min_strength}`.
- **Scoring:** `score(question: dict, result: AgentResult) -> Score`, where `Score` is a frozen
  dataclass with:
  - `kind_ok: bool`: the analysis kinds used match `analysis`, and `"none"` means no analysis;
  - `outcome: Literal["correct", "wrong", "abstained"]`;
  - `failed: list[str]`: the names of failed checks.
- **`spec.json`:** a hand-written `DatasetSpec` for `orders.csv`, so building needs no model call.
- **`run.py` CLI:**

  ```
  uv run python -m eval.analysis.run --output-dir eval/reports/analysis-<label> --max-total-tokens N [--attempts 3] [--replay PATH | --record]
  ```

  - **Build:** ingest and build in the sandbox with `spec.json`, reusing the steps of
    `eval/datamate/prepare.py`.
  - **Run:** run every question with
    `run_graph(analysis=True, interactive=False, charts=True)`, using the LLM client set up as
    in `eval/datamate/run.py`.
  - **Output:** write `results.jsonl` and `metrics.json` with the manifest, and print:
    - kind right;
    - correct, wrong, and abstained;
    - verify problems;
    - tool error kinds;
    - latency;
    - cost.

- [ ] **Step 1: Write the failing scorer tests** with hand-made `AgentResult`s, one per check
  type and outcome:
  - `test_top_segment_passes_on_the_evidence_analysis`;
  - `test_using_analysis_on_a_control_is_kind_wrong`;
  - `test_an_unanswerable_answer_is_abstained`;
  - `test_ranges_must_match_the_same_days`.

  Plus `test_the_generator_is_deterministic_and_plants_the_effects`. Run the generator into
  `tmp_path` twice and expect identical bytes. Recompute October against September by branch
  from the CSV: Q1's share is between 60% and 80%.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/test_analysis_eval.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the generator, spec, scorer, and runner. Generate the files**

Run: `uv run python eval/analysis/generate.py`
Expected: it prints the planted answers and writes `shop/orders.csv` and `questions.jsonl`.

- [ ] **Step 4: Run the tests, then build the dataset at $0**

Run: `uv run pytest tests/unit/test_analysis_eval.py -v`, then
`uv run python -m eval.analysis.run --output-dir eval/reports/analysis-build --build-only`
Expected: PASS, then a built dataset with a parse rate of 100% and no quarantined rows.

- [ ] **Step 5: Ask the user before the real runs.**
  - The analysis set three times on Groq `gpt-oss-20b` costs about $0.10–0.30.
  - `smoke` with `--agent graph --analysis` against `--agent graph` costs about $0.05–0.10.

  Report each run's cost. Compare totals against the exit criteria in the spec:
  - kind right ≥ 8/10;
  - correct ≥ 7/10;
  - no untraced number;
  - controls ≥ 5/6 with no analysis;
  - `smoke` not worse beyond noise.

- [ ] **Step 6: Commit** the eval code and the generated files. Commit no reports; they are
  gitignored.

```bash
git add eval/analysis tests/unit/test_analysis_eval.py
git commit -m "feat: synthetic eval for fixed analyses with planted effects"
```

---

### Task 13: Record it

**Files:**
- Create: `docs/adr/0051-fixed-analyses.md`, `docs/learning/28-phan-tich-co-dinh.md`
- Modify: `docs/adr/README.md`, `docs/learning/README.md`, `docs/architecture.md`,
  `docs/threat-model.md`, `docs/progress.md`, and the spec's status line

- [ ] **Step 1: Write ADR 0051.**
  - **Context:** the archive's ADRs 0032 and 0033 were never on main, and their lessons came from
    that branch.
  - **Decision:** five kinds computed by code; analyses ride on `QueryRecord`; analysis off keeps
    requests byte-identical; the forced kind.
  - **Consequences:** the measured image size and latency, plus the memory result from Task 4.
  - **Rejected:** `run_python`, rate and mix decomposition for now, and clicking a chart point.
- [ ] **Step 2: Update `architecture.md`:**
  - Tools: `analyze` in the list, and `run_python` still not built;
  - Verification: analyses are evidence;
  - Runner: `/v1/stats`;
  - Output: `QueryRecord.analysis` and analysis charts.
- [ ] **Step 3: Update `threat-model.md`:** the new Runner endpoint, the libraries added to the
  sandbox, and the fact that no model code runs.
- [ ] **Step 4: Write learning note 28** in Vietnamese: the trusted functions pattern, why not a
  code interpreter, additive decomposition and why "where" is not "why", period handling, and
  likely interview questions. Add it to the learning index.
- [ ] **Step 5: Update `progress.md`** (under 60 lines) with the eval result and cost, and set
  the spec status to implemented.
- [ ] **Step 6: Run everything once more**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest && (cd web && npm test && npm run typecheck && npm run lint)`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add docs
git commit -m "docs: fixed analyses (ADR 0051, note 28)"
```
