# Architecture

DA-Agent turns uploaded tabular files and a free-form description into a queryable dataset, then
answers questions about it with cited, verified results.

This document describes the intended design. Most of it is not implemented yet. See
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

Steps 1–6 run inside a sandbox container, so untrusted bytes never reach a process that holds
secrets ([ADR 0009](adr/0009-parse-uploads-in-sandbox.md)).

1. **Safety checks:** limits on size, file type, and row count. Reject zip bombs. Ignore macros and
   junk files such as `Zone.Identifier`.
2. **Raw read:** xlsx, csv (with encoding detection), and SQLite become raw text tables.
3. **Table detection:** find header rows, drop title rows, split by sheet.
4. **Profile:** per-column statistics and value patterns, plus rare row shapes (sets of filled
   columns) with example rows, which point at subtotal and note rows.
5. **LLM proposes a `DatasetSpec`** from the profile and the user's description: column types and
   roles, parsing, fill-down and dedupe rules, rules that exclude non-record rows, relationships,
   glossary, business rules, data time range, sensitive columns.
6. **Code validates the spec:** dry-run it and report parse rates. Unparseable values go to a
   `_quarantine` table and excluded rows to an `_excluded` table, so nothing is silently dropped.
   Parse rates under 98%, and exclude rules that look like they remove records, go back to the LLM,
   up to 3 attempts. These values held on all 24 dev shops ([ADR 0010](adr/0010-exclude-non-record-rows.md)).
   Code removes exact repeats when a key of one or two columns proves them double entries, keeps
   source column names that are already identifiers, and lists the values of text columns with
   few of them for the agent ([ADR 0012](adr/0012-schema-names-values-and-double-entries.md)).
7. **The user approves the spec** in the UI. Evals auto-approve.
8. **Load into DuckDB:** the agent queries only cleaned views. Original columns stay available for
   cross-checking.

`DatasetSpec` is declarative data interpreted by our code. It never contains executable code. There
is no fixed canonical schema and no DataMate-specific code ([ADR 0006](adr/0006-dataset-spec.md)).

Implemented in `src/da_agent/ingestion/`:
- `raw.py` (steps 1–4, sandbox job `ingest`), `propose.py` (step 5, Worker), `build.py` (steps 6
  and 8, sandbox job `build`), and `pipeline.py`, which repairs a spec up to 3 times.
- Code, not the model, settles what code can tell: groups of three digits always mean thousands,
  and a column cannot be both day-first and month-first.
- Columns the raw profile marks sensitive are always hidden. The spec can hide more, never fewer.
- Invalid parts of a proposal are dropped one column at a time and sent back for repair.
- A parse rate shows how many values parsed, not whether they parsed correctly. A wrong number
  format once read `22,000` as 22 at a 99% parse rate.

## Agent graph

```
plan ─┬─ ambiguous and interactive ─> clarify (interrupt, at most once) ─> plan
      └─> agent ⇄ tools ─(submit_answer)─> verify ─┬─ pass ────────────────> answer
            ^                                     ├─ fail, repairs left (2) ┘
            └─────────────────────────────────────└─ fail, none left ───────> abstain
```

| Node | Job |
|---|---|
| `plan` | One model call: steps, ambiguity, and a question for the user, from the question, documents, and schema |
| `clarify` | Interrupt and wait for the user, in interactive runs only |
| `agent` | One model turn of the bounded tool loop |
| `tools` | Run that turn's tool calls; all read-only |
| `verify` | Run code checks and route the result |
| `answer` | Return the verified `AgentAnswer` |
| `abstain` | Stop with a reason: limits, budget, or an answer that failed verification |

Rules ([ADR 0013](adr/0013-agent-graph.md)):

- Every loop has hard limits: tool iterations, repair attempts, tokens, and money.
- Routing is code. The LLM supplies judgment, not control flow that code can decide.
- Each model turn is a checkpoint. A resume repeats at most the running turn, and tools only read.
- A provider failure leaves the run open at its last checkpoint, so a retry continues it.
- Eval runs never interrupt: the plan follows the documents' defaults for ambiguous questions.

### State

Now: `question, as_of, clarifications, plan_steps, messages, steps, iterations, nudges, repairs,
proposed, verified, answer, stop_reason, error`. Later: `workspace_id, dataset_id, artifact_refs`.
The dataset, model client, and tracer are runtime context, not state.

State stays small and serializable. Large results are stored as artifacts, and state keeps only a
preview and a reference. Each field has one owner node, and accumulating fields have explicit
reducers.

### Tools

`describe_dataset`, `profile_column`, `search_rules`, `run_sql`, `run_python`, `make_chart`,
`ask_user`, `submit_answer`.

Tools have Pydantic input and output schemas and bounded output. Errors come back as structured
results the model can act on, and do not crash the graph.

### Verification

Mostly code, not LLM self-assessment ([ADR 0007](adr/0007-code-first-verification.md)). Done:

- An answer needs a successful query, and every number in it must match a query result within
  rounding.
- Arithmetic on typed numbers, such as `SELECT 11.0 / 913 * 100`, is allowed only when those
  numbers came back from queries that read the data. Scale constants (0, 1, 100) and ROUND's
  digits are exempt.
- The queries behind the answer filter dates to a period the data covers. Periods are read from
  the SQL with sqlglot, and the build records each date column's range, which `describe_dataset`
  also shows. A period entirely outside the data means no rows, not 0.
- Joins do not repeat the rows an answer aggregates. For each SELECT that sums, averages, or
  counts across a join, one read-only probe with the same FROM, JOIN, and WHERE compares how
  often each table's rows appear with how many there are (`count(rowid)` against
  `count(DISTINCT rowid)`). An aggregate fails only when every table it reads repeats, so a
  lookup price repeated per line passes. A probe that errors is skipped.
- No row the queries read has a value the build could not parse. Clean rows keep `_source` and
  `_row`, and `_quarantine` lists failed values by the same keys, so a probe counts them inside
  the query's rows. A hit withholds the answer without a repair ([ADR 0014](adr/0014-probe-checks-in-verify.md)).

Planned for the reliability phase:

- Important answers are recomputed a second way.

### Memory and RAG

- **Short-term:** thread state in checkpoints.
- **Long-term, per dataset:** the approved spec, rules, and user corrections, each with provenance.
- **RAG:** dataset docs and rules. Rules that shape every answer stay in the prompt. Topic sections
  are listed by title and fetched with `search_rules`, by title or keywords
  ([ADR 0008](adr/0008-rules-on-demand.md)). Embedding retrieval is tried later and measured with
  evals.

### Output

`AgentAnswer` holds a kind, a value, and an optional reason. `AgentResult` adds the steps taken, and
its successful queries are the evidence behind the answer. The `eval/datamate` adapter converts it to the competition format, and
the core does not know that format. Charts are validated Vega-Lite specs with no code execution.
Reports are standalone HTML, and every number links to the query that produced it.

## Failure handling

| Failure | Expected behaviour |
|---|---|
| Worker killed mid-run | Another worker resumes from the last checkpoint and does not repeat finished steps |
| LLM 429 or timeout | Retry with backoff, then fall back to another provider, then fail the run cleanly |
| Tool or sandbox crash | Structured tool error returned to the model, within the iteration limit. The model sees the error type only; the trace keeps the exception |
| Sandbox cannot start (Docker down) | The Runner returns an error result instead of raising; verification probes that crash are skipped |
| Budget exceeded | Stop cleanly with partial work and a clear status |
| Verification keeps failing | Abstain after 2 repair attempts instead of returning an unverified answer |
| Answer reads values the build could not parse | Withhold it, naming the column and the row count |
| Malformed upload | Reject or quarantine with a report that the user can read |

## Open questions

| Question | Decide by |
|---|---|
| Add a sandboxed code-transform step beside the declarative spec, for layouts the spec cannot express (pivots, several tables in one sheet)? Its output must pass the same row accounting and parse-rate checks. Kept out for now ([ADR 0006](adr/0006-dataset-spec.md)) | When a dataset needs it |
