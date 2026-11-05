# Architecture

DA-Agent turns uploaded tabular files and a free-form description into a queryable dataset, then
answers questions about it with cited, verified results.

This document describes the intended design. **None of it is implemented yet.** See
[progress.md](progress.md) for what exists. Security controls are in
[threat-model.md](threat-model.md), and the reasons behind major choices are in [adr/](adr/).

## System overview

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

## Components

| Component | Responsible for | Must never |
|---|---|---|
| Web (React) | Upload, spec approval, chat, chart and trace views | Hold provider keys, or render model output as trusted HTML |
| API (FastAPI) | Auth, workspace scoping, enqueueing jobs, SSE, rate limits | Run generated code or call LLMs in request handlers |
| Worker | Claiming jobs, ingestion pipeline, running the graph | Run generated code, or hold the Docker socket |
| LLMClient | Provider calls, retries, fallback, caching, cost records | Be bypassed: nodes never call provider SDKs directly |
| Runner | Starting sandboxes, enforcing limits, returning bounded results | Accept anything other than a run request for a known dataset |
| Sandbox | Running one SQL query or Python snippet | Have network access, secrets, other datasets, or eval answers |
| Postgres | Jobs, checkpoints, datasets, audit log, cost ledger | Store large results: those are artifacts on the volume |
| Phoenix | Traces and spans | Receive unmasked sensitive columns |

## Module map

| Path | Contents |
|---|---|
| `src/da_agent/api/` | FastAPI routes, auth, SSE |
| `src/da_agent/worker/` | Job claiming and execution |
| `src/da_agent/runner/` | Runner service and sandbox lifecycle |
| `src/da_agent/agent/` | Graph, nodes, state, tools, verification |
| `src/da_agent/ingestion/` | Readers, table detection, profiling, `DatasetSpec`, loader |
| `src/da_agent/llm/` | `LLMClient`, provider adapters, price table |
| `src/da_agent/storage/` | Postgres access and the artifact store |
| `src/da_agent/security/` | SQL guard and masking |
| `src/da_agent/telemetry/` | OpenTelemetry setup |
| `web/` | React + Vite app |
| `sandbox/` | Sandbox image |
| `deploy/` | Compose files and deployment config |
| `examples/` | Generated demo data |
| `eval/scorer/` | Scorer, the only code allowed to read `answers.jsonl` |
| `eval/datamate/` | Adapter from `AgentAnswer` to the competition format |
| `eval/splits/` | Shop splits and question sets |
| `eval/reports/` | Raw run output, gitignored because it can contain answers and cell values |
| `docs/eval/` | Published aggregate results |
| `tests/` | `unit/`, `integration/` (Docker or Postgres), `security/` (boundary attacks) |

Dependencies point inward: `api`, `worker`, and `runner` may use `agent`, `ingestion`, `llm`,
`storage`, and `security`. Nothing under `src/` imports from `eval/`.

## Request flow

1. The client uploads files. The API stores them and enqueues an ingestion job.
2. The Worker runs ingestion and produces a proposed `DatasetSpec` with a validation report.
3. The user approves or edits the spec, and the Worker loads the data into DuckDB.
4. The client asks a question. The API enqueues a run and streams progress over SSE.
5. The Worker runs the graph, sending SQL and Python to the Runner.
6. The result is a structured `AgentAnswer` with citations to tool results and artifacts.

## Ingestion

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

`DatasetSpec` is declarative data interpreted by our code. It never contains executable code. There
is no fixed canonical schema and no DataMate-specific code ([ADR 0006](adr/0006-dataset-spec.md)).

## Agent graph

```
understand → plan → execute (bounded tool loop) → verify ─┬─ pass ────────> answer
    │                  ^                                  ├─ wrong ───────> back to execute (max 2)
    │                  └──────────────────────────────────┘
    ├─ ambiguous ──> clarify (interrupt, wait for user)   └─ missing data ─> abstain (with reason)
```

| Node | Job |
|---|---|
| `understand` | Interpret the question and detect ambiguity |
| `plan` | Choose the steps and data needed |
| `execute` | Run a bounded tool loop |
| `verify` | Run code checks and route the result |
| `answer` | Build `AgentAnswer` from verified results |
| `clarify` | Interrupt and wait for the user |
| `abstain` | Explain what data is missing |

Rules:

- Every loop has hard limits: tool iterations, repair attempts, tokens, and money.
- Routing is code. The LLM supplies judgment, not control flow that code can decide.
- A node does one job and is safe to resume. Side effects are idempotent or keyed by `run_id` and
  step, so a checkpoint resume does not repeat them.

### State

`run_id, workspace_id, dataset_id, question, as_of, plan, steps, artifact_refs, verification, budget,
status`

State stays small and serializable. Large results are stored as artifacts, and state keeps only a
preview and a reference. Each field has one owner node, and accumulating fields have explicit
reducers.

### Tools

`describe_dataset`, `profile_column`, `search_rules`, `run_sql`, `run_python`, `make_chart`,
`ask_user`, `submit_answer`.

Tools have Pydantic input and output schemas and bounded output. Errors come back as structured
results the model can act on, and do not crash the graph.

### Verification

Mostly code, not LLM self-assessment ([ADR 0007](adr/0007-code-first-verification.md)):

- The asked period falls inside the data range.
- Joins do not multiply rows.
- Parse rates of the columns used are acceptable.
- Every number in the answer appears in a tool result.
- Important answers are recomputed a second way.

### Memory and RAG

- **Short-term:** thread state in checkpoints.
- **Long-term, per dataset:** the approved spec, rules, and user corrections, each with provenance.
- **RAG:** dataset docs and rules. The first version puts the whole rulebook in the prompt, which
  helps caching. Retrieval is tried later and measured with evals.

### Output

`AgentAnswer` is structured. The `eval/datamate` adapter converts it to the competition format, and
the core does not know that format. Charts are validated Vega-Lite specs with no code execution.
Reports are standalone HTML, and every number links to the query that produced it.

## Failure handling

| Failure | Expected behaviour |
|---|---|
| Worker killed mid-run | Another worker resumes from the last checkpoint and does not repeat finished steps |
| LLM 429 or timeout | Retry with backoff, then fall back to another provider, then fail the run cleanly |
| Tool or sandbox crash | Structured tool error returned to the model, within the iteration limit |
| Budget exceeded | Stop cleanly with partial work and a clear status |
| Verification keeps failing | Abstain after 2 repair attempts instead of returning an unverified answer |
| Malformed upload | Reject or quarantine with a report that the user can read |

## Open questions

| Question | Decide by |
|---|---|
| Should file parsing run in a sandbox? Today's design parses untrusted files in the Worker, which holds provider keys ([threat-model.md](threat-model.md#residual-risks-and-open-items)) | Day 3, before ingestion |
| How does the Worker call the Runner: internal HTTP or a job table? | Days 3–6 |
| One container per request, or one per run? This trades startup latency against isolation | Days 3–6 |
| What are the parse rate thresholds and the retry limit N? Set from real data | Days 3–6 |
| Which fields are in `AgentAnswer` and how are citations shaped? | Day 2, with the minimal agent |
