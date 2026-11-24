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
| [0027](0027-tailwind-and-shadcn-ui.md) | Tailwind CSS and shadcn/ui for the web UI | Accepted |
| [0028](0028-release-images-to-ghcr.md) | Version tags publish the images to GHCR, checked by a smoke test on the published images | Accepted |
| [0029](0029-code-excludes-proven-subtotals.md) | Code excludes subtotal rows it can prove, beyond the model's rules | Accepted |
| [0030](0030-conversation-and-dataset-memory.md) | Conversation and dataset memory in Postgres, written only by people | Accepted |
| [0031](0031-guard-signals-by-kind.md) | Guard signals are counted by kind in traces, events, eval rows, and the dashboard | Accepted |
| [0032](0032-result-export.md) | Result export is a job that writes the whole result in a sandbox | Accepted |
| [0033](0033-edited-sql-as-a-query-job.md) | Edited SQL runs as a query job, checked before it is queued | Accepted |
| [0034](0034-column-profile-at-build.md) | The column profile is computed during the build and kept in the dataset summary | Accepted |
| [0035](0035-plain-explanation-from-sql.md) | An answer is explained in plain words by reading its SQL in code, not by the model | Accepted |
| [0036](0036-metric-definitions.md) | Metric definitions are saved from an answer's SQL and read as approved memory | Accepted |
| [0037](0037-alternative-readings-rejected.md) | Other readings of a business word are not offered with the answer | Rejected |
| [0038](0038-run-manifest.md) | Every job attempt records the agent version that ran it | Accepted |
| [0039](0039-record-replay.md) | Eval runs can record model outcomes and replay them at no cost | Accepted |
| [0040](0040-pass-k.md) | pass^k measures how stable eval answers are across runs | Accepted |
| [0041](0041-llm-resilience.md) | A circuit breaker per process and outbound limits per route for model calls | Accepted |
| [0042](0042-cancel-deadline-time-travel.md) | Runs stop cooperatively on a cancel, a deadline, or a lost lease; operators time travel | Accepted |
| [0043](0043-feedback-goldens.md) | User feedback, golden cases from thumbs-ups, and feedback rates per agent version | Accepted |
| [0044](0044-slo-alerts.md) | SLOs and alerts with Prometheus metrics from the processes and Alertmanager | Accepted |
| [0045](0045-dataset-versions.md) | Adding files to a dataset creates a new version inside the same dataset | Accepted |
| [0046](0046-vietnamese-ui.md) | The UI offers Vietnamese next to English, from typed dictionaries in the web | Accepted |
| [0047](0047-answer-language.md) | Code picks the answer language from the question; the model is told it | Accepted |
| [0048](0048-oneshot-baseline.md) | A one-shot text-to-SQL baseline measures what the agent adds | Accepted |
| [0049](0049-garbled-tool-calls.md) | Garbled tool names are read back by code; a malformed call gets one retry in its turn | Accepted |
