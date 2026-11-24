# Operations

How to run the backend for development without Compose, and the operator tools: time travel over
checkpoints, golden reruns, SLO alerts, and traces. The [README](../README.md) covers the Compose
quick start.

## Running the backend without Compose

For development. Without `DA_AGENT_RUNNER_URL` the worker starts sandbox containers itself, so it
needs Docker and the sandbox image; Compose runs a separate Runner service instead
([ADR 0024](adr/0024-runner-service.md)). Postgres holds jobs, checkpoints, and costs.

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

## Time travel over checkpoints

Operators can read and rerun a run's checkpoints with the worker's settings
([ADR 0042](adr/0042-cancel-deadline-time-travel.md)); a fork calls the model:

```bash
uv run python -m da_agent.timetravel list $RUN               # every step of a run
uv run python -m da_agent.timetravel show $RUN $CHECKPOINT   # the state at one step
uv run python -m da_agent.timetravel fork $RUN $CHECKPOINT   # run on from there in a new thread
```

## Golden reruns

Answers users rated 👍 become golden cases, which operators rerun after a change
([ADR 0043](adr/0043-feedback-goldens.md)); `run` spends only with `--yes`:

```bash
uv run python -m da_agent.goldens list --workspace $WORKSPACE      # cases and runs rated 👎
uv run python -m da_agent.goldens run --workspace $WORKSPACE --yes # same / changed / error
```

## SLOs and alerts

Compose also runs Prometheus (`127.0.0.1:9090`) and Alertmanager (`127.0.0.1:9093`) for SLOs and
alerts ([ADR 0044](adr/0044-slo-alerts.md)). Set `DA_AGENT_ALERT_WEBHOOK_URL` in `.env` to
receive alerts; rules are in `deploy/prometheus/rules/` and their tests run with `uv run pytest`.

## Traces

Traces go to any OpenTelemetry (OTLP) endpoint set in `DA_AGENT_OTLP_ENDPOINT`, such as a local
Phoenix (`http://127.0.0.1:6006/v1/traces`). Setting `DA_AGENT_TRACE_UI_URL=http://127.0.0.1:6006`
on the API adds an "Open trace" link to each run in the UI. Phoenix shows every workspace's
traces, so set it only where the operator is the only user.

## Settings

Every setting is listed in [`src/da_agent/config.py`](../src/da_agent/config.py). The
interactive API reference is at `/docs`.
