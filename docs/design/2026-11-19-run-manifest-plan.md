# Run Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every job attempt and every eval run records which agent configuration produced it, and a
run resumed from a checkpoint written by another version is marked.

**Architecture:** A pure module `da_agent.manifest` builds an `AgentManifest` (code version, prompt
fingerprints, routes, limits) and its short version id. The worker registers it once in
`agent_versions` and writes a `job_attempts` row per claim; the graph stamps the version into
LangGraph checkpoint metadata, and `RunHandler` compares it on resume. Eval writes the manifest into
`metrics.json`.

**Tech Stack:** Python 3.12, psycopg 3, LangGraph 1.2.20 / langgraph-checkpoint 4.3.1, OpenTelemetry,
pytest, Docker.

**Spec:** [2026-11-19-run-manifest.md](2026-11-19-run-manifest.md)

## Global Constraints

- Branch `feat/run-manifest` from `main` (clean at de6f1b4). Commit after each task; push only when
  the user asks. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests first (`tdd` skill). `uv run pytest` and `uv run ruff check .` pass after every task.
  Integration tests need `DA_AGENT_TEST_POSTGRES_URL` (`uv run --env-file .env pytest`).
- No secrets in a manifest: no API keys, tokens, or database URLs.
- No model calls anywhere in this plan ($0).
- Core code (`src/`) never imports from `eval/`.
- Next numbers on `main`: migration `0013`, ADR `0038`, learning note `17`.
- Span attributes: `da_agent.agent_version`, `gen_ai.agent.version`, `da_agent.resumed_from_version`.
- Version id: first 12 hex characters of SHA-256 over the canonical JSON of the manifest
  (`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`). Prompt
  fingerprints: first 16 hex characters of SHA-256.

## Review Focus

1. A route's API key, or an env var holding one, ends up in the manifest JSON: it must never appear
   (test in Task 1).
2. A worker image built without the build arg, with no `.git`: the sha is `unknown` and the worker
   starts normally (test in Task 1).
3. A checkpoint written before this change has no version: the resume is not marked (test in Task 4).
4. Tracing is off: the attempt row is still written, with a null trace id (test in Task 3).
5. Two workers with the same configuration: one version id and one `agent_versions` row, whatever
   the key order of their settings (tests in Tasks 1 and 2).

---

### Task 1: The manifest builder

**Files:**
- Create: `src/da_agent/manifest.py`
- Modify: `src/da_agent/llm/adapters/openai_compatible.py` (add a read-only `options` property)
- Test: `tests/unit/test_manifest.py`

**Interfaces:**
- Produces:
  - `AGENT_PROMPT_MODULES: tuple[ModuleType, ...]` = `agent.prompts, agent.planning, agent.tools,
    agent.charts, agent.graph, agent.memory, agent.answer`
  - `INGESTION_PROMPT_MODULES: tuple[ModuleType, ...]` = `ingestion.propose, ingestion.pipeline`
  - `prompt_fingerprint(modules: Sequence[ModuleType], samples: Sequence[str] = ()) -> str`
  - `agent_prompt_samples() -> list[str]`
  - `code_version(env: Mapping[str, str], repo: Path = REPO_ROOT) -> dict[str, Any]` returning
    `{"sha": str, "dirty": bool | None}`
  - `route_summary(routes: Sequence[Route]) -> list[dict[str, Any]]`
  - `@dataclass(frozen=True) class AgentManifest` with fields `code: dict[str, Any]`,
    `prompts: dict[str, str]`, `routes: list[dict[str, Any]]`, `limits: dict[str, Any]`, a
    `version` property (str, 12 hex), and `to_json() -> dict[str, Any]` (the four fields plus
    `"version"`).
  - `build_manifest(*, routes: Sequence[Route], limits: Mapping[str, Any], env: Mapping[str, str], repo: Path = REPO_ROOT) -> AgentManifest`
  - `OpenAICompatibleAdapter.options -> dict[str, Any]`: `{"extra_body": ..., "min_output_tokens": ..., "timeout": ...}`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_same_configuration_always_gets_the_same_version() -> None:
    a = build_manifest(
        routes=[route("groq", "m")], limits={"x": 1, "y": 2}, env={"DA_AGENT_GIT_SHA": "abc"}
    )
    b = build_manifest(
        routes=[route("groq", "m")], limits={"y": 2, "x": 1}, env={"DA_AGENT_GIT_SHA": "abc"}
    )
    assert a.version == b.version and len(a.version) == 12


def test_any_field_changes_the_version() -> None:
    base = dict(routes=[route("groq", "m")], limits={"x": 1}, env={"DA_AGENT_GIT_SHA": "abc"})
    v = build_manifest(**base).version
    assert build_manifest(**{**base, "limits": {"x": 2}}).version != v
    assert build_manifest(**{**base, "routes": [route("groq", "other")]}).version != v
    assert build_manifest(**{**base, "env": {"DA_AGENT_GIT_SHA": "def"}}).version != v


def test_no_secret_reaches_the_manifest() -> None:
    adapter = OpenAICompatibleAdapter(
        provider="groq", base_url="https://x/v1", api_key="sk-SECRET-123"
    )
    manifest = build_manifest(
        routes=[Route(adapter, "m")],
        limits={},
        env={"GROQ_API_KEY": "sk-SECRET-123", "DA_AGENT_GIT_SHA": "abc"},
    )
    assert "SECRET" not in json.dumps(manifest.to_json())


def test_route_options_are_recorded_in_order() -> None:
    vllm = OpenAICompatibleAdapter(
        provider="vllm", base_url="http://v/v1", api_key="EMPTY", extra_body={"seed": 0}
    )
    summary = route_summary([Route(vllm, "gemma"), route("groq", "m")])
    assert [(r["provider"], r["model"]) for r in summary] == [("vllm", "gemma"), ("groq", "m")]
    assert summary[0]["options"]["extra_body"] == {"seed": 0}


def test_the_build_arg_wins_over_git(tmp_path: Path) -> None:
    assert code_version({"DA_AGENT_GIT_SHA": "abc"}, tmp_path) == {"sha": "abc", "dirty": None}


def test_without_git_or_build_arg_the_sha_is_unknown(tmp_path: Path) -> None:
    assert code_version({}, tmp_path) == {"sha": "unknown", "dirty": None}


def test_a_local_checkout_reports_its_sha_and_whether_it_is_dirty(tmp_path: Path) -> None:
    # git init, commit one file with -c user.name/-c user.email, then:
    clean = code_version({}, tmp_path)
    assert clean["sha"] == head_sha(tmp_path) and clean["dirty"] is False
    (tmp_path / "f.txt").write_text("changed")
    assert code_version({}, tmp_path)["dirty"] is True


def test_a_prompt_constant_changes_its_fingerprint(monkeypatch) -> None:
    before = prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples())
    monkeypatch.setattr(prompts, "NUDGE", prompts.NUDGE + " ")
    assert prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples()) != before
    assert len(before) == 16


def test_lowercase_names_and_unplain_values_do_not_count(monkeypatch) -> None:
    before = prompt_fingerprint([prompts])
    monkeypatch.setattr(prompts, "helper_text", "x", raising=False)
    monkeypatch.setattr(prompts, "SOME_PATTERN", re.compile("x"), raising=False)
    assert prompt_fingerprint([prompts]) == before


def test_the_manifest_has_one_fingerprint_per_prompt_family() -> None:
    m = build_manifest(routes=[route("groq", "m")], limits={}, env={"DA_AGENT_GIT_SHA": "abc"})
    assert set(m.prompts) == {"agent", "ingestion"}
```

`route(provider, model)` is a local helper returning `Route(FakeAdapter(provider), model)` where
`FakeAdapter` has a `provider` attribute and no `options`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_manifest.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'da_agent.manifest'`

- [ ] **Step 3: Implement `src/da_agent/manifest.py` and `OpenAICompatibleAdapter.options`**

- `prompt_fingerprint`: for each module in the order given, then each name in sorted order, take
  names that are `isupper()` and do not start with `_`, convert the value with a recursive
  `_plain` (str, int, float, bool, list/tuple of plain values, dict with str keys and plain values,
  pydantic `BaseModel` through `model_dump(mode="json")`), skip the name when any part is not
  plain (compiled patterns, functions), and hash `[module.__name__, name, value]` triples plus the
  samples as canonical JSON.
- `agent_prompt_samples()`: `turns_left_notice(1)`, `turns_left_notice(WARN_AT_TURNS_LEFT)`,
  `invalid_tool_call_notice(["tool"])`, `plan_system_prompt([], True)`,
  `plan_system_prompt([], False)`, `build_system_prompt([])`,
  `build_system_prompt([], schema_given=True)`, without the `None` results.
- `code_version`: `DA_AGENT_GIT_SHA` from env first. Otherwise `git -C repo rev-parse HEAD` and
  `git -C repo status --porcelain`, `subprocess.run(..., capture_output=True, text=True, timeout=5, check=True)`;
  dirty means the status output is not empty. Any `OSError`, `CalledProcessError`, or
  `TimeoutExpired` gives `{"sha": "unknown", "dirty": None}`. `REPO_ROOT = Path(__file__).resolve().parents[2]`.
- `route_summary`: `{"provider": adapter.provider, "model": route.model, "options": getattr(adapter, "options", {})}`.
  Do not read anything else from the adapter or the env.
- `build_manifest`: prompts are `{"agent": prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples()), "ingestion": prompt_fingerprint(INGESTION_PROMPT_MODULES)}`; limits are copied as given.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/unit/test_manifest.py tests/unit/test_llm_adapters.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/manifest.py src/da_agent/llm/adapters/openai_compatible.py tests/unit/test_manifest.py
git commit -m "feat: build an agent manifest and its version id"
```

### Task 2: Store versions and attempts

**Files:**
- Create: `src/da_agent/storage/migrations/0013_agent_versions.sql`
- Create: `src/da_agent/storage/versions.py`
- Test: `tests/integration/test_versions.py`

**Interfaces:**
- Produces:
  - Tables `agent_versions (id text PRIMARY KEY, manifest jsonb NOT NULL, first_seen timestamptz NOT NULL DEFAULT now())`
    and `job_attempts (job_id uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE, attempt integer NOT NULL, worker_id text NOT NULL, agent_version text REFERENCES agent_versions (id), trace_id text, resumed_from_version text, started_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (job_id, attempt))`.
    `resumed_from_version` has no foreign key: an old checkpoint may name a version this database
    never registered.
  - `@dataclass(frozen=True) class Attempt`: `attempt: int`, `worker_id: str`,
    `agent_version: str | None`, `trace_id: str | None`, `resumed_from_version: str | None`
  - `register_version(conn: psycopg.Connection, version: str, manifest: dict[str, Any]) -> None`
    (`ON CONFLICT (id) DO NOTHING`)
  - `record_attempt(conn, job_id: UUID, attempt: int, worker_id: str, agent_version: str | None, trace_id: str | None) -> None`
    (`ON CONFLICT (job_id, attempt) DO NOTHING`)
  - `mark_resumed_from(conn, job_id: UUID, attempt: int, version: str) -> None`
  - `attempts_of(conn, job_id: UUID) -> list[Attempt]`, in attempt order

- [ ] **Step 1: Write the failing tests**

```python
def test_registering_a_version_twice_keeps_one_row(conn) -> None:
    register_version(conn, "abc123def456", {"code": {"sha": "x"}})
    register_version(conn, "abc123def456", {"code": {"sha": "x"}})
    assert conn.execute("SELECT count(*) FROM agent_versions").fetchone()[0] == 1

def test_attempts_are_listed_in_order_with_their_version(conn, workspace) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    register_version(conn, "v1", {})
    record_attempt(conn, job_id, 1, "w1", "v1", "trace-a")
    record_attempt(conn, job_id, 2, "w2", None, None)
    assert attempts_of(conn, job_id) == [
        Attempt(1, "w1", "v1", "trace-a", None), Attempt(2, "w2", None, None, None)]

def test_recording_the_same_attempt_again_changes_nothing(conn, workspace) -> None: ...
    # record (job, 1, "w1", None, "t1") then (job, 1, "w9", None, "t9"): the first row stays

def test_a_resume_mark_lands_on_its_attempt(conn, workspace) -> None:
    # record attempts 1 and 2; mark_resumed_from(conn, job_id, 2, "old")
    # attempts_of(...)[1].resumed_from_version == "old" and [0] stays None

def test_deleting_a_job_deletes_its_attempts(conn, workspace) -> None: ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --env-file .env pytest tests/integration/test_versions.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'da_agent.storage.versions'`

- [ ] **Step 3: Write the migration and `storage/versions.py`**

A header comment in the migration names the design doc and ADR 0038, like the other migrations.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run --env-file .env pytest tests/integration/test_versions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/storage/migrations/0013_agent_versions.sql src/da_agent/storage/versions.py tests/integration/test_versions.py
git commit -m "feat: store agent versions and job attempts"
```

### Task 3: The worker records each attempt and its version

**Files:**
- Modify: `src/da_agent/worker/worker.py` (`WorkerConfig`, `Worker.run_once`)
- Modify: `src/da_agent/worker/__main__.py` (`build_worker`, `main`)
- Test: `tests/integration/test_worker.py`, `tests/integration/test_entrypoints.py`

**Interfaces:**
- Consumes: `build_manifest`, `AgentManifest.version`, `AgentManifest.to_json()` (Task 1);
  `register_version`, `record_attempt`, `attempts_of` (Task 2).
- Produces: `WorkerConfig.agent_version: str | None = None`. `build_worker` returns a worker whose
  `config.agent_version` is the registered version.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_worker.py
def test_each_attempt_is_recorded_with_its_worker_version_and_trace(
    schema, conn, workspace
) -> None:
    # tracing on (InMemorySpanExporter), register_version(conn, "v1", {}),
    # worker config agent_version="v1", a handler that raises RetryableJobError, retry_delays=(0,)
    # run_once twice
    attempts = attempts_of(conn, job_id)
    assert [(a.attempt, a.worker_id, a.agent_version) for a in attempts] == [
        (1, "w1", "v1"),
        (2, "w1", "v1"),
    ]
    assert [a.trace_id for a in attempts] == job(conn, job_id, workspace).trace_ids
    root = [s for s in exporter.get_finished_spans() if s.name == "job.run"][0]
    assert root.attributes["da_agent.agent_version"] == "v1"
    assert root.attributes["gen_ai.agent.version"] == "v1"


def test_without_tracing_the_attempt_is_still_recorded(schema, conn, workspace) -> None:
    # trace.NoOpTracer(), no agent_version
    assert attempts_of(conn, job_id) == [Attempt(1, "w1", None, None, None)]


# tests/integration/test_entrypoints.py
def test_the_worker_registers_its_version(tmp_path, schema, conn) -> None:
    env = {"DA_AGENT_ROUTES": "ollama:m", "DA_AGENT_GIT_SHA": "abc"}  # plus what settings() needs
    worker = build_worker(settings(tmp_path, schema, **env), env, ledger_conn=conn)
    row = conn.execute("SELECT id, manifest FROM agent_versions").fetchone()
    assert row[0] == worker.config.agent_version
    assert row[1]["code"]["sha"] == "abc"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --env-file .env pytest tests/integration/test_worker.py tests/integration/test_entrypoints.py -v`
Expected: the three new tests FAIL (`WorkerConfig` has no `agent_version`; no `agent_versions` row).

- [ ] **Step 3: Implement**

- `Worker.run_once`: after `add_trace`, call `record_attempt(conn, job.id, job.attempts, self.worker_id, self.config.agent_version, trace_id)`,
  with `trace_id` `None` when the span context is invalid. When `agent_version` is set, put it on
  the job span as both attributes.
- `build_worker`: build the routes once and reuse them for `LLMClient`. Limits:
  `{"agent": asdict(AgentLimits()), "daily_usd": settings.daily_budget_usd, "run_usd": settings.run_budget_usd, "run_tokens": settings.run_max_tokens}`.
  Call `register_version(conn, manifest.version, manifest.to_json())`, pass `agent_version` to
  `WorkerConfig` and to `RunHandler` (the parameter is added in Task 4; until then only
  `WorkerConfig` gets it).
- `main`: the start line prints `worker {id} (agent {version}) waiting for jobs`.

- [ ] **Step 4: Run the worker and entrypoint tests**

Run: `uv run --env-file .env pytest tests/integration/test_worker.py tests/integration/test_entrypoints.py -v`
Expected: PASS, including the existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/worker/worker.py src/da_agent/worker/__main__.py tests/integration/test_worker.py tests/integration/test_entrypoints.py
git commit -m "feat: the worker records each attempt with its agent version"
```

### Task 4: Checkpoints carry the version, and a resume by another version is marked

**Files:**
- Modify: `src/da_agent/agent/graph.py` (`run_graph`)
- Modify: `src/da_agent/agent/checkpoints.py` (add `checkpoint_version`)
- Modify: `src/da_agent/worker/runs.py` (`RunHandler`)
- Modify: `src/da_agent/worker/__main__.py` (pass `agent_version` to `RunHandler`)
- Test: `tests/unit/test_agent_graph.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Consumes: `mark_resumed_from`, `attempts_of` (Task 2); `WorkerConfig.agent_version` (Task 3).
- Produces:
  - `run_graph(..., agent_version: str | None = None)`: when set, the config is
    `{"configurable": {"thread_id": ...}, "metadata": {"agent_version": agent_version}}`, and the
    `agent.run` span gets `da_agent.agent_version` and `gen_ai.agent.version`.
  - `checkpoint_version(saver: BaseCheckpointSaver, thread_id: str) -> tuple[bool, str | None]`:
    whether a checkpoint exists, and the `agent_version` in its metadata.
  - `RunHandler(..., agent_version: str | None = None)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_agent_graph.py
def test_checkpoints_carry_the_agent_version(dataset) -> None:
    saver = InMemorySaver()
    run(dataset, [plan(), sql(), submit("number", 350)], checkpointer=saver, agent_version="v1")
    assert checkpoint_version(saver, "r1") == (True, "v1")

def test_a_thread_without_checkpoints_has_no_version() -> None:
    assert checkpoint_version(InMemorySaver(), "none") == (False, None)

# tests/integration/test_worker.py
def test_a_run_resumed_by_another_version_is_marked(schema, conn, workspace, dataset) -> None:
    # versions "old" and "new" registered; handler A (agent_version="old") on worker "dead"
    # is killed after plan and sql (Killed() in the script); the lease expires;
    # handler B (agent_version="new") on worker "alive" finishes the run.
    attempts = attempts_of(conn, job_id)
    assert [a.resumed_from_version for a in attempts] == [None, "old"]

def test_a_run_resumed_by_the_same_version_is_not_marked(...) -> None:
    # same as above, both handlers "v1": [None, None]

def test_a_checkpoint_from_before_versions_is_not_marked(...) -> None:
    # handler A has agent_version=None, handler B "new": [None, None]
```

`run()` in `test_agent_graph.py` gains an `agent_version: str | None = None` keyword passed to
`run_graph`. `run_handler()` in `test_worker.py` gains the same keyword passed to `RunHandler`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --env-file .env pytest tests/unit/test_agent_graph.py tests/integration/test_worker.py -v -k "version or resumed"`
Expected: FAIL (`run_graph` has no `agent_version`; no `checkpoint_version`).

- [ ] **Step 3: Implement**

- `checkpoint_version`: `saver.get_tuple({"configurable": {"thread_id": thread_id}})`; `None` gives
  `(False, None)`; otherwise `(True, metadata.get("agent_version"))`.
- `RunHandler.__call__`: after `saver.setup()` and before `run_graph`, read
  `checkpoint_version(saver, str(job.id))`. Mark only when a checkpoint exists, its version is not
  `None`, and it differs from `self.agent_version`: call
  `mark_resumed_from(events_conn, job.id, job.attempts, previous)` and set
  `da_agent.resumed_from_version` on the current span. Pass `agent_version` to `run_graph`.
- `build_worker`: pass `agent_version=manifest.version` to `RunHandler`.

- [ ] **Step 4: Run the full suite**

Run: `uv run --env-file .env pytest && uv run ruff check .`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/da_agent/agent/graph.py src/da_agent/agent/checkpoints.py src/da_agent/worker/runs.py src/da_agent/worker/__main__.py tests/unit/test_agent_graph.py tests/integration/test_worker.py
git commit -m "feat: checkpoints carry the agent version and resumes across versions are marked"
```

### Task 5: Eval runs write the manifest

**Files:**
- Modify: `eval/datamate/run.py` (`RunConfig`, `run_questions`, CLI `main`)
- Test: `tests/unit/test_datamate_eval.py`

**Interfaces:**
- Consumes: `build_manifest`, `AgentManifest.to_json()` (Task 1).
- Produces: `RunConfig.manifest: dict[str, Any] | None = None`. When set, `metrics["manifest"]`
  holds it, and `metrics.json` with it.

- [ ] **Step 1: Write the failing test**

```python
def test_the_manifest_is_written_into_metrics(data_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    llm, ledger, _ = make_llm([answer(1)])
    questions = read_jsonl(data_dir / "questions.jsonl")[:1]
    manifest = {"version": "abc123def456", "code": {"sha": "x", "dirty": True}}
    run_questions(questions, data_dir, llm, ledger, RunConfig(output, manifest=manifest))
    saved = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert saved["manifest"] == manifest
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_datamate_eval.py -v -k manifest`
Expected: FAIL, `RunConfig` has no `manifest`.

- [ ] **Step 3: Implement**

`run_questions` copies `config.manifest` into `metrics` before writing. The CLI builds it with
`build_manifest(routes=routes, limits={"agent": asdict(AgentLimits(max_iterations=..., budget=RunBudget(max_total_tokens=...)))}, env=os.environ)`
from the same arguments it already passes, and prints the version on its first output line.

- [ ] **Step 4: Run the eval tests**

Run: `uv run pytest tests/unit/test_datamate_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add eval/datamate/run.py tests/unit/test_datamate_eval.py
git commit -m "feat: eval runs record the agent manifest"
```

### Task 6: Images know their git sha

**Files:**
- Modify: `deploy/app.Dockerfile` (app stage)
- Modify: `compose.yml` (`x-app.build`, and the runner's build)
- Modify: `.github/workflows/release.yml` (build step)

- [ ] **Step 1: Add the build arg**

In the app stage, after the last `uv sync` and before `USER`, so a new sha does not reinstall
dependencies: `ARG DA_AGENT_GIT_SHA=unknown` and `ENV DA_AGENT_GIT_SHA=$DA_AGENT_GIT_SHA`. Compose
passes `args: {DA_AGENT_GIT_SHA: "${DA_AGENT_GIT_SHA:-unknown}"}`. The release build adds
`--build-arg DA_AGENT_GIT_SHA=$GITHUB_SHA` (the web and sandbox images ignore an unused arg).

- [ ] **Step 2: Verify**

Run: `docker build -f deploy/app.Dockerfile --target app --build-arg DA_AGENT_GIT_SHA=$(git rev-parse HEAD) -t da-agent:manifest-check . && docker run --rm da-agent:manifest-check python -c "import os; from da_agent.manifest import code_version; print(code_version(os.environ))"`
Expected: `{'sha': '<the HEAD sha>', 'dirty': None}`. Without `--build-arg`: `{'sha': 'unknown', 'dirty': None}`.

Run: `docker compose config | grep DA_AGENT_GIT_SHA`
Expected: the arg appears under both app builds.

- [ ] **Step 3: Commit**

```bash
git add deploy/app.Dockerfile compose.yml .github/workflows/release.yml
git commit -m "build: images carry their git sha for the agent manifest"
```

### Task 7: Docs

**Files:**
- Create: `docs/adr/0038-run-manifest.md`, `docs/learning/17-run-manifest.md` (Vietnamese)
- Modify: `docs/adr/README.md`, `docs/learning/README.md`, `docs/architecture.md`
  (worker section and a short "Versions" paragraph under Sign-in and operations),
  `docs/design/2026-11-19-run-manifest.md` (status: built), `docs/progress.md`

- [ ] **Step 1: Write ADR 0038** from the design's Approaches table: decision, the three rejected
  options, consequences (resume policy C left open; the mark measures how often it is needed).
- [ ] **Step 2: Write learning note 17** in the note frame of `docs/learning/README.md`: what a
  manifest is and why traces need a version; why the fingerprint hashes what the model sees; why
  checkpoint metadata and not the previous attempt; interview questions (how do you attribute a
  quality regression to a change; what happens to in-flight runs during a deploy).
- [ ] **Step 3: Update architecture.md and progress.md**, keeping progress under 60 lines.
- [ ] **Step 4: Verify** `uv run pytest && uv run ruff check . && uv run ruff format --check .`
  Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: ADR 0038 and note 17 for the run manifest"
```
