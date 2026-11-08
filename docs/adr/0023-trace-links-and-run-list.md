# 0023: Operators open each run's traces from the UI, and runs are listed by the API

**Status:** Accepted, 2026-11-08

**Context:** The plan asks for a trace view in the UI. Traces serve the operator: monitoring,
debugging, and demos. End users do not need them. Phoenix holds the traces, and it has no
workspace isolation: anyone who can open it sees every workspace's prompts and the cell values in
them. A run can have several traces, because a retried or resumed job starts a new `job.run`
span. The UI also kept its run history in the browser, since the API had no way to list runs.

**Decision:**
- Keep Phoenix. The code only emits OpenTelemetry over OTLP, so Langfuse or LangSmith are a
  configuration change (endpoint and auth header), not a rewrite. Phoenix is one container and
  keeps data on the machine. Self-hosted Langfuse needs Postgres, ClickHouse, Redis, and blob
  storage, about 13 GiB of RAM at minimum. LangSmith is closed, self-hosted only on Enterprise,
  and would send prompts with cell values to a third party.
- The worker appends the trace id of each attempt's `job.run` span to `jobs.trace_ids`, written
  only by the worker that holds the job. Without a tracing provider the span is not recording
  and nothing is stored.
- `GET /v1/runs/{id}` returns `traces: [{attempt, url}]` only when `DA_AGENT_TRACE_UI_URL` is set,
  and an empty list otherwise. Links use Phoenix's own `/redirects/traces/<trace id>` route, which
  finds the trace in its project. The UI shows one "Open trace" link per attempt, and always shows
  the run id, which matches the `da_agent.job_id` span attribute.
- `GET /v1/runs?dataset_id=&limit=&before=` lists a dataset's runs in the caller's workspace,
  newest first, at most 50 a page. `next_before` is the creation time to pass for the next page.
  A dataset outside the workspace is "not found", as everywhere else in the API.

**Consequences:**
- Turning on trace links is an operator decision. On a shared or public deployment the variable
  must stay unset, or every user could reach other workspaces' traces through Phoenix.
- An `UPDATE` per claimed job records its trace; it is a single indexed row write.
- Two runs created in the same microsecond could straddle a page boundary; the API creates runs
  one request at a time, so this is accepted.
- Answering a clarifying question from the UI stays open: the graph can resume with a reply, but
  no endpoint sends one yet.

**Rejected:**
- A trace panel built into the UI from the cost ledger: safe across workspaces, but it repeats
  what Phoenix already shows, for a view only the operator uses.
- Showing only the run id: no risk, but a demo then needs a manual search in Phoenix.
- Storing one trace id in the run result: lost for attempts that fail, and wrong after a resume.
