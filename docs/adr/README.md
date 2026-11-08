# Architecture decision records

One short file per decision with real trade-offs. Do not rewrite an accepted ADR. To change a
decision, add a new ADR and mark the old one as superseded.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-langgraph.md) | LangGraph for orchestration | Accepted |
| [0002](0002-custom-llm-client.md) | Custom `LLMClient` with no LangChain model wrappers | Accepted |
| [0003](0003-postgres-job-queue.md) | Job queue on Postgres | Accepted |
| [0004](0004-duckdb-per-dataset.md) | One DuckDB file per dataset | Accepted |
| [0005](0005-sandbox-behind-runner.md) | Docker sandbox behind a Runner service | Accepted |
| [0006](0006-dataset-spec.md) | LLM-proposed, user-approved `DatasetSpec` | Accepted |
| [0007](0007-code-first-verification.md) | Verification mostly in code | Accepted |
| [0008](0008-rules-on-demand.md) | Topic rules fetched on demand with `search_rules` | Accepted |
| [0009](0009-parse-uploads-in-sandbox.md) | Uploaded files are parsed inside the sandbox | Accepted |
| [0010](0010-exclude-non-record-rows.md) | Declarative rules exclude subtotal and note rows | Accepted |
| [0011](0011-sql-per-request-sandbox.md) | Agent SQL runs in a new sandbox per query, called as a library | Accepted |
| [0012](0012-schema-names-values-and-double-entries.md) | Keep source column names, list known values, and remove proven double entries | Accepted |
| [0013](0013-agent-graph.md) | The agent graph: one plan call, per-turn checkpoints, and code verification | Accepted |
| [0014](0014-probe-checks-in-verify.md) | Verification probes the data for join fan-out and unreadable values | Accepted |
| [0015](0015-charts-built-by-code.md) | Charts are Vega-Lite specs that code builds from a query | Accepted |
| [0016](0016-html-reports.md) | Reports link every number to the query cell the agent read | Accepted |
| [0017](0017-queue-leases-and-sql-migrations.md) | Job leases on the database clock, and plain SQL migrations | Accepted |
| [0018](0018-worker.md) | The worker: heartbeat thread, error classes, and the job id as the graph thread | Accepted |
| [0019](0019-dataset-lifecycle.md) | Dataset lifecycle: ingest proposes, the user approves, runs open only ready datasets | Accepted |
| [0020](0020-http-api.md) | The HTTP API: bearer keys, uploads by safe name, and "not found" across workspaces | Accepted |
| [0021](0021-progress-events.md) | Run progress as events in Postgres, streamed with SSE | Accepted |
| [0022](0022-rate-and-money-limits.md) | Rate limits and daily money limits in Postgres | Accepted |
| [0023](0023-trace-links-and-run-list.md) | Operators open each run's traces from the UI, and runs are listed by the API | Accepted |
| [0024](0024-runner-service.md) | The Runner runs as its own service when deployed, the only holder of the Docker socket | Accepted |
| [0025](0025-browser-sessions-and-login-links.md) | Browsers sign in with one-time links and keep a cookie session; keys stay for scripts | Accepted |
| [0026](0026-operator-dashboard.md) | An operator dashboard in the web UI, built on Postgres aggregates | Accepted |
