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

Moved to [architecture.md](architecture.md). Security controls are in
[threat-model.md](threat-model.md), and the reasons for major choices are in [adr/](adr/README.md).

## 3. Evaluation

- **Scorer:** implements `train/rulebook.md` §9 exactly: format, alias, and +1 / −1 / 0 scoring.
- **Split by shop:** 24 dev and 6 holdout, balanced by data completeness. Holdout runs once, at the
  end.
  Shops already read closely during design stay in dev, so holdout stays unseen.
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
