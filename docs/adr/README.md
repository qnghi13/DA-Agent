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
