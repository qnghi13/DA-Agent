# DA-Agent

An open-source agent that analyses uploaded CSV, Excel, and SQLite files. You upload data with a
free-form description and ask questions. It answers with results you can trace back to the queries
that produced them.

> **Status: early development.** The agent, ingestion, sandbox, HTTP API, web UI, and Docker
> Compose packaging work and are tested. Expect rough edges.

## Quick start

Requires Docker Engine 26 or later with the Compose plugin, and a [Groq](https://console.groq.com)
API key (free tier).

```bash
cp .env.example .env          # then fill in the three required values
docker compose up -d --build  # the first build takes a few minutes
docker compose exec api python -m da_agent.admin create-workspace "My shop"   # prints a key once
```

Open http://127.0.0.1:8080, paste the key, and upload the three files in `examples/demo-shop/`.
Approve the proposed spec, then ask "What was the revenue in June 2026?" (8,714,000 VND).
Traces are in Phoenix at http://127.0.0.1:6006.

Before you upload your own data, know what leaves the machine: column profiles, sample values,
and query results go to the model provider. Sandboxes are containers (runc) without network
access, which is not a hard boundary against kernel exploits. See the
[threat model](docs/threat-model.md).

`deploy/smoke.sh` checks a running stack without calling a model. `docker compose down` stops it;
add `-v` to delete all data.

## Planned features

- Handles messy, unknown spreadsheets through a reviewable dataset specification.
- Runs generated SQL and Python in an isolated sandbox.
- Verifies answers with code checks, and abstains when the data is insufficient.
- Resumes runs after a crash, and enforces hard cost limits.
- Evaluation suite, with tracing through OpenTelemetry and Phoenix.
- Self-hosted with Docker Compose.

## Documentation

- [Architecture](docs/architecture.md)
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
uv run python -m da_agent.api --port 8000
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
npm run dev          # http://localhost:5173, then paste the workspace key
npm test             # unit tests (vitest)
npm run build        # type-check and bundle into web/dist
```

## License

[Apache-2.0](LICENSE)
