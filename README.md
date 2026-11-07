# DA-Agent

An open-source agent that analyses uploaded CSV, Excel, and SQLite files. You upload data with a
free-form description and ask questions. It answers with results you can trace back to the queries
that produced them.

> **Status: early development.** The agent, ingestion, sandbox, and HTTP API work and are tested.
> The web UI and Docker Compose packaging are not built yet.

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

## Running the backend

The worker starts sandbox containers, so it needs Docker and the sandbox image. Postgres holds
jobs, checkpoints, and costs.

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
```

Every setting is listed in [`src/da_agent/config.py`](src/da_agent/config.py). The interactive API
reference is at `/docs`.

## Running the web UI

The UI needs Node 22. In development Vite proxies `/v1` to the API, so both share one origin.
Set `DA_AGENT_API_URL` if the API is not on `http://127.0.0.1:8000`.

```bash
cd web
npm ci
npm run dev          # http://localhost:5173, then paste the workspace key
npm run build        # type-check and bundle into web/dist
```

## License

[Apache-2.0](LICENSE)
