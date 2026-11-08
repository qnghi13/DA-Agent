# DA-Agent

An open-source agent that analyses uploaded CSV, Excel, and SQLite files. You upload data with a
free-form description and ask questions. It answers with results you can trace back to the queries
that produced them.

> **Status:** a learning and portfolio project, complete for its plan: agent, ingestion, sandbox,
> HTTP API, web UI, operator dashboard, and Docker Compose packaging work and are tested. It has not
> run in production.

## Quick start

Requires Docker Engine 26 or later with the Compose plugin, and a [Groq](https://console.groq.com)
API key (free tier).

```bash
cp .env.example .env          # then fill in the three required values
docker compose up -d --build  # the first build takes a few minutes
docker compose exec api python -m da_agent.admin create-workspace "My shop"   # prints its id and a key
docker compose exec api python -m da_agent.admin login-link --workspace WORKSPACE_ID
```

Open the printed link within 15 minutes; it signs the browser in and works once. Upload the three
files in `examples/demo-shop/`, approve the proposed spec, then ask "What was the revenue in June
2026?" (8,714,000 VND). "Invite or add a device" in the UI makes more links for the workspace.

For the operator dashboard (queue, outcomes, latency, model errors, cost against budgets, recent
failures), run `login-link --operator` and open that link. Traces are in Phoenix at
http://127.0.0.1:6006. If the UI is not served from http://127.0.0.1:8080, set
`DA_AGENT_PUBLIC_URL` to its address: links point there, and the API refuses changes from other
origins.

Before you upload your own data, know what leaves the machine: column profiles, sample values,
and query results go to the model provider. Sandboxes are containers (runc) without network
access, which is not a hard boundary against kernel exploits. See the
[threat model](docs/threat-model.md).

`deploy/smoke.sh` checks a running stack without calling a model. `docker compose down` stops it;
add `-v` to delete all data.

## How it works

```mermaid
flowchart LR
    browser[Browser: React UI] -- "HTTP, SSE" --> api[API: FastAPI]
    api -- jobs --> pg[(Postgres: jobs, checkpoints,<br/>audit, costs)]
    worker[Worker: ingestion + LangGraph] -- claims jobs --> pg
    worker --> llm[LLMClient] --> providers[Model providers]
    worker -- "SQL only" --> runner[Runner]
    runner --> sandbox[Sandbox container:<br/>no network, no secrets,<br/>one read-only dataset]
```

- **Messy files become a reviewable spec.** Code profiles each upload inside a sandbox. The model
  proposes a declarative `DatasetSpec` (types, number and date formats, rows to exclude,
  sensitive columns), code dry-runs it and reports parse rates, and the user approves it before
  anything is queried ([ADR 0006](docs/adr/0006-dataset-spec.md)).
- **Generated SQL runs in a sandbox.** A SQL guard allows read-only queries, and each query runs in
  a container without network or secrets that sees one dataset
  ([ADR 0009](docs/adr/0009-parse-uploads-in-sandbox.md),
  [ADR 0011](docs/adr/0011-sql-per-request-sandbox.md)).
- **Answers are checked by code.** Numbers must come from query results, periods must fall inside
  the data, joins must not double-count, and unparseable values withhold the answer. Failed checks
  get two repairs, then the agent abstains ([ADR 0007](docs/adr/0007-code-first-verification.md)).

```mermaid
flowchart LR
    plan --> agent
    plan -. "ambiguous, interactive" .-> clarify -.-> plan
    agent <--> tools
    agent -- submit_answer --> verify
    verify -- pass --> answer
    verify -- "fail, repairs left" --> agent
    verify -- "fail, none left" --> abstain
```

- **Runs survive crashes and stay within budget.** Every model turn is a Postgres checkpoint, so a
  killed worker's run continues without repeating finished steps. Token and money limits apply per
  run and per workspace.
- **Operable.** OpenTelemetry traces in Phoenix, a cost ledger per model call, an audit log, and an
  operator dashboard for queue health, outcomes, latency, model errors, and cost.
- **Measured.** An evaluation harness with a separate scorer process, fixed dev and holdout
  splits, and a cost report for every run. See [Evaluation](#evaluation).

## Evaluation

On the [DataMate](docs/eval/README.md) benchmark of messy Vietnamese retail data, scored +1
correct, −1 wrong, 0 abstained, with Groq `gpt-oss-20b` and the full pipeline from raw files:

| Set | Correct | Wrong | Abstained | Score | Cost |
|---|---|---|---|---|---|
| 150 dev questions (24 shops) | 62% | 27% | 11% | +53 | $0.38 |
| 150 holdout questions (6 unseen shops, run once) | 60% | 31% | 9% | +44 | $0.30 |

Holdout is within noise of dev. Details, the history of changes, and caveats are in
[docs/eval](docs/eval/README.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Evaluation results](docs/eval/README.md)
- [Threat model](docs/threat-model.md)
- [Decision records](docs/adr/README.md)
- [Plan and roadmap](docs/plan.md)
- [Current progress](docs/progress.md)
- [Learning notes, in Vietnamese](docs/learning/README.md)

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run pytest
uv run ruff check .
```

Tests that need Postgres are skipped unless `DA_AGENT_TEST_POSTGRES_URL` is set; each test runs in
its own schema.

The project was built with Claude Code. `CLAUDE.md`, `.claude/settings.json`, the hooks, agents,
and the `tdd` and `agent-architecture` skills are part of the repo. Third-party skills are not
committed; restore them if you want the same setup (needs Node):

```bash
npx skills experimental_install          # the versions pinned in skills-lock.json
playwright-cli install --skills          # from @playwright/cli, for browser checks of the UI
```

## Running the backend without Compose

For development. Without `DA_AGENT_RUNNER_URL` the worker starts sandbox containers itself, so it
needs Docker and the sandbox image; Compose runs a separate Runner service instead
([ADR 0024](docs/adr/0024-runner-service.md)). Postgres holds jobs, checkpoints, and costs.

```bash
docker build -f sandbox/Dockerfile -t da-agent-sandbox:dev .
export DA_AGENT_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/da_agent
export DA_AGENT_DATA_DIR=/path/to/data            # uploads, staging, and built datasets
export DA_AGENT_ROUTES=groq:openai/gpt-oss-20b    # the worker only; GROQ_API_KEY too

uv run python -m da_agent.admin create-workspace "My shop"   # prints a key once
DA_AGENT_PUBLIC_URL=http://localhost:5173 uv run python -m da_agent.api --port 8000
uv run python -m da_agent.admin login-link --workspace WORKSPACE_ID   # for the browser
uv run python -m da_agent.worker
```

Then, with the key as a bearer token:

```bash
curl -H "Authorization: Bearer $KEY" -F name=Sales -F files=@sales.csv localhost:8000/v1/datasets
curl -H "Authorization: Bearer $KEY" localhost:8000/v1/datasets/$DATASET        # review the spec
curl -H "Authorization: Bearer $KEY" -X POST -H 'Content-Type: application/json' -d '{}' \
     localhost:8000/v1/datasets/$DATASET/approve
curl -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
     -d "{\"dataset_id\": \"$DATASET\", \"question\": \"Total revenue in June?\"}" localhost:8000/v1/runs
curl -N -H "Authorization: Bearer $KEY" localhost:8000/v1/runs/$RUN/events     # live progress
curl -H "Authorization: Bearer $KEY" localhost:8000/v1/runs/$RUN/report > report.html
curl -H "Authorization: Bearer $KEY" "localhost:8000/v1/runs?dataset_id=$DATASET"  # history
```

Traces go to any OpenTelemetry (OTLP) endpoint set in `DA_AGENT_OTLP_ENDPOINT`, such as a local
Phoenix (`http://127.0.0.1:6006/v1/traces`). Setting `DA_AGENT_TRACE_UI_URL=http://127.0.0.1:6006`
on the API adds an "Open trace" link to each run in the UI. Phoenix shows every workspace's
traces, so set it only where the operator is the only user.

Every setting is listed in [`src/da_agent/config.py`](src/da_agent/config.py). The interactive API
reference is at `/docs`.

## Running the web UI

The UI needs Node 22. In development Vite proxies `/v1` to the API, so both share one origin.
Set `DA_AGENT_API_URL` if the API is not on `http://127.0.0.1:8000`.

```bash
cd web
npm ci
npm run dev          # http://localhost:5173; open a login link (the API needs
                     # DA_AGENT_PUBLIC_URL=http://localhost:5173)
npm test             # unit tests (vitest)
npm run build        # type-check and bundle into web/dist
```

## License

[Apache-2.0](LICENSE)
