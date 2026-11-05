# DA-Agent plan

The approved plan: what we are building and why. Current state and the next step live in
[progress.md](progress.md). Read only the section you need.

Change this file only when a decision changes, and record the reason in `docs/adr/` once that
directory exists.

| Section | Read it when |
|---|---|
| [1. Decisions](#1-decisions) | Checking what is already settled |
| [2. Architecture](#2-architecture) | Working on services, ingestion, the graph, or security |
| [3. Evaluation](#3-evaluation) | Working on the scorer, splits, or eval runs |
| [4. LLM strategy](#4-llm-strategy) | Choosing a model or spending money |
| [5. Repo layout](#5-repo-layout) | Adding a new module or directory |
| [6. Roadmap](#6-roadmap) | Starting a phase, or checking its exit criteria |
| [7. Non-goals](#7-non-goals) | A task looks like scope creep |
| [8. Verification](#8-verification) | Finishing a phase |

## 1. Decisions

The project teaches Agent System Engineering and serves as a portfolio piece for an AI Engineer
role. Architecture, reliability, and operability are the goals. Scores only detect regressions.

| Topic | Decision |
|---|---|
| Product | Anyone can upload arbitrary data files with a free-form description |
| Release | Apache-2.0 open source, run with `docker compose up`. VPS hosting is postponed |
| Working mode | Claude writes most of the code. The user asks about anything unclear |
| Orchestration | LangGraph from the start |
| UI | React (Vite) |
| Users | Workspace + API key. No sign-up or login |
| Language | Code, README, and architecture docs in English. Learning notes in Vietnamese. The agent answers in the asker's language |
| LLM | Free tiers first, then a small Haiku 5.5 budget to measure quality |

**Machine:** uv, Node 22, Docker, 7.6 GB RAM, 4 GB GPU. No `gh` or `jq`.

**Data in `train/` (187 MB, gitignored):** 30 shops, 3,000 answered questions, `rulebook.md`,
`calendar.csv`, 107 xlsx, 21 csv, 1 SQLite. It is messy:

- Title rows above the header (biz_124).
- Numbers such as `"22,000"` and `25000k`, and quantities such as `1 hộp`.
- Dates as Excel serials mixed with `dd/mm/yyyy`.
- Multiple sheets, missing order IDs (biz_107, biz_113), no line items (biz_110).

## 2. Architecture

```
React ──HTTP/SSE──> FastAPI ──> Postgres (jobs, state, checkpoints, audit, costs)
                                   │ job
                                   v
                         Worker (ingestion + LangGraph) ──> LLMClient ──> Groq/Gemini/Claude/vLLM
                                   │ code and SQL execution requests only
                                   v
                         Runner ──> Sandbox container (no network, no secrets)
                                   └─ Sees only its own dataset, read-only

Across all services: OpenTelemetry → Phoenix
```

**Stack:** Python 3.12, uv, Ruff, pyright, pytest, FastAPI, Pydantic, LangGraph with the Postgres
checkpointer, DuckDB, sqlglot, Vega-Lite, React + Vite + TypeScript.

Notable choices:

- **Job queue on Postgres (`FOR UPDATE SKIP LOCKED`), no Redis:** fewer services, fits 7.6 GB RAM,
  easy to self-host.
- **Files on a volume behind an interface:** no MinIO yet.
- **One DuckDB file per dataset:** clear isolation between workspaces.
- **Custom `LLMClient`, no LangChain model wrappers:** LangGraph does not require them, and owning
  the client gives control over caching, cost, and retries. Each provider uses its native SDK, with
  one shared adapter for OpenAI-compatible endpoints (Groq, vLLM, Ollama, DeepSeek).

### 2.1 Ingestion

1. **Safety checks:** limits on size, file type, and row count. Reject zip bombs. Ignore macros and
   junk files such as `Zone.Identifier`.
2. **Raw read:** xlsx, csv (with encoding detection), and SQLite become raw text tables.
3. **Table detection:** find header rows, drop title rows, split by sheet.
4. **Profile:** per-column statistics and value patterns.
5. **LLM proposes a `DatasetSpec`** from the profile and the user's description: column types and
   roles, parsing, fill-down and dedupe rules, relationships, glossary, business rules, data time
   range, sensitive columns.
6. **Code validates the spec:** dry-run it and report parse rates. Unparseable values go to a
   quarantine table and are never silently dropped. Low parse rates go back to the LLM, up to N
   times.
7. **The user approves the spec** in the UI. Evals auto-approve.
8. **Load into DuckDB:** the agent queries only cleaned views. Original columns stay available for
   cross-checking.

There is no fixed canonical schema and no DataMate-specific code.

### 2.2 Agent graph (LangGraph)

```
understand → plan → execute (bounded tool loop) → verify ─┬─ pass ────────> answer
    │                  ^                                  ├─ wrong ───────> back to execute (max 2)
    │                  └──────────────────────────────────┘
    ├─ ambiguous ──> clarify (interrupt, wait for user)   └─ missing data ─> abstain (with reason)
```

- **State:** `run_id, workspace_id, dataset_id, question, as_of, plan, steps, artifact_refs,
  verification, budget, status`. Large results are stored as artifacts. State holds only a preview
  and a reference.
- **Tools:** `describe_dataset`, `profile_column`, `search_rules`, `run_sql`, `run_python`,
  `make_chart`, `ask_user`, `submit_answer`.
- **Verification is mostly code:**
  - The asked period falls inside the data range.
  - Joins do not multiply rows.
  - Parse rates of the columns used are acceptable.
  - Every number in the answer appears in a tool result.
  - Important answers are recomputed a second way.
- **Memory:** short-term is thread state in checkpoints. Long-term is per dataset: the approved spec,
  rules, and user corrections, each with provenance.
- **RAG:** dataset docs and rules. The first version puts the whole rulebook in the prompt, which
  helps caching. Retrieval is tried later and measured with evals.
- **Output:** a structured `AgentAnswer`. The `eval/datamate` adapter converts it to the competition
  format. The core does not know that format.
- **Charts and reports:** validated Vega-Lite specs, no code execution, rendered in React. Reports
  are standalone HTML, and every number links to the query that produced it.

### 2.3 Security

| Risk | Control |
|---|---|
| LLM-generated code | Container with `--network none`, read-only, non-root, `--cap-drop ALL`, RAM, CPU, PID, and time limits, no secrets. Only the Runner holds the Docker socket |
| SQL | sqlglot allows only `SELECT`. DuckDB opens read-only with `enable_external_access` off and a timeout |
| Prompt injection from cells or descriptions | File content is always framed as data. An attack test suite exists. Least-privilege tools limit the damage of a successful injection |
| Eval answer leakage | `answers.jsonl` is never mounted into a sandbox. The scorer runs in a separate process |
| Personal data (`cccd`, phone, email) | Sensitive columns are masked in prompts and logs. Columns marked hidden do not appear in views |
| Runaway cost | Token and money limits per run, day, and workspace. Exceeding one stops the run cleanly |
| Cross-workspace access | Every query carries `workspace_id`. A test proves one workspace cannot read another's data |

gVisor is not installed. Document the limits of a runc sandbox and keep gVisor optional.

## 3. Evaluation

- **Scorer:** implements `train/rulebook.md` §9 exactly: format, alias, and +1 / −1 / 0 scoring.
- **Split by shop:** 24 dev and 6 holdout, balanced by data completeness. Holdout runs once, at the
  end.
- **Question sets within dev:** `smoke` (30), `regression` (150, stratified by answer type and
  shop), and the full set only when budget allows.
- **Metrics:** score; correct, wrong, and abstain rates; cost and latency per question; tool errors;
  repair count; failure cause.
- **Beyond DataMate:** 1–2 non-retail datasets to check generality, plus injection and sandbox escape
  suites.
- **CI:** PRs use recorded LLM responses at no cost. Real evals run manually or on a schedule.
  Results are published in `docs/eval/`.

## 4. LLM strategy

1. **Start free:** Groq primary, Gemini Flash-Lite fallback, Ollama for flow tests. Send only demo or
   DataMate data.
2. **Day 2:** run `smoke` on biz_123 to see whether free models handle tool calls and SQL. Free-tier
   limits came from secondary sources, so check them directly.
3. **Days 7–11, when `regression` is needed:** add $5–10 of Haiku 5.5. Tell the user first.
4. **Optional at the end:** rent a GPU, run vLLM, and compare with the API on the same eval set.

## 5. Repo layout

```
src/da_agent/
  api/  worker/  runner/          # services
  agent/                          # graph, nodes, state, tools, verify
  ingestion/                      # readers, detect, profile, spec, loader
  llm/                            # LLMClient, adapters, price table
  storage/  security/  telemetry/
web/                              # React + Vite
sandbox/                          # sandbox Dockerfile
eval/                             # scorer, splits, datamate adapter, reports
tests/  deploy/  examples/        # examples: generated demo data
docs/
  plan.md  progress.md            # this plan and the current state
  architecture.md  threat-model.md  adr/
  learning/                       # Vietnamese notes
```

`train/` stays in place and gitignored. Code reads it through `DATAMATE_DIR`. The public repo holds
only generated demo data.

## 6. Roadmap

| Days | Work | Exit criteria |
|---|---|---|
| **1–2 Foundation** | Git, tooling, docs, ADRs, threat model, scorer and eval splits, `LLMClient`, tracing, cost ledger, minimal agent on biz_123 | Scorer has tests. `smoke` produces a score and cost. Traces open in Phoenix. Free model chosen |
| **3–6 Data and tools** | Ingestion, `DatasetSpec`, DuckDB, SQL guard, Runner and sandbox | 30/30 shops load, with parse and quarantine reports. All sandbox escape tests are blocked. `smoke` baseline across several shops |
| **7–10 Agent workflow** | Full graph, checkpoints, interrupts, memory, tools, `regression` baseline | Killing a worker mid-run resumes without repeating finished steps. Clarify and abstain behave correctly. Baseline exists |
| **11–13 Reliability** | Verification, repair loop, retries, cost limits, injection suite, failure analysis | Before and after verification comparison on `regression`. Simulated 429s, timeouts, and tool crashes are handled. Injection causes no harm |
| **14–15 Charts, reports, RAG** | Vega-Lite, HTML reports, retrieval experiment | Sample report traces every number. RAG compared with the full rulebook in the prompt |
| **16–17 Backend** | FastAPI, workspace + API key, job queue, SSE, audit log, rate limit | Upload and ask through the API end to end. Workspace isolation tests pass |
| **18–19 React** | Upload and spec approval, streaming chat, plan, code, chart, and trace views | Full demo flow works in a browser |
| **20 Packaging** | Compose, images, CI/CD, dashboard, demo data | `docker compose up` works on a clean machine. CI is green |
| **21 Finish** | Holdout run, README, diagrams, demo video, eval report | Repo is ready to publish |

Each milestone gets a Vietnamese note in `docs/learning/`: the concept, why this choice, rejected
options, and likely interview questions. LangGraph notes also explain the internals the framework
hides: reducers, checkpoints, interrupts.

**Schedule risk:** 21 days is tight with arbitrary files and React. If late, cut in this order:
self-host comparison → RAG experiment → dashboard → report export → non-DataMate datasets. Never cut
the sandbox, verification, evals, or cost limits.

## 7. Non-goals

Kubernetes, SSO, account sign-up and login, multi-agent, multi-region, connecting to a user's external
database, fine-tuning, tuning for the competition score.

## 8. Verification

- **Unit:** scorer, parsers, SQL guard, verification.
- **Security:** sandbox attempts at network, file writes, env reads, fork bombs, memory exhaustion. SQL
  attempts with `COPY`, `read_csv`, `ATTACH`. Injection through cells and descriptions. Cross-workspace
  access.
- **Fault tolerance:** worker killed mid-run, LLM 429 or timeout, tool crash, budget exceeded.
- **End to end:** `docker compose up` on a clean environment, upload demo data in the UI, approve the
  spec, ask a question, view the chart, report, and trace.
- **Quality:** `smoke` and `regression` against the baseline, then holdout once at the end.
