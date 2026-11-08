# 0026: An operator dashboard in the web UI, built on Postgres aggregates

**Status:** Accepted, 2026-11-09

**Context:** Phoenix shows one run at a time: its steps, prompts, tokens, and timing. The
operator also needs the whole system at a glance: whether answers come back, how long runs take,
what they cost and for whom, whether the queue is stuck, and whether the provider is failing.
Almost all of it is already in Postgres (`jobs`, `llm_calls`, `run_events`, `audit_log`,
`datasets`, `workspaces`). Failed model calls are the exception: the ledger records only
successful calls.

**Decision:**
- **Who:** operator sessions only (ADR 0025). Members and API keys get 404 on `/v1/ops/*`.
- **Where:** a `/ops` page in the same React app, polling every 30 s. Charts use Vega-Lite, which
  the UI already ships. No new service.
- **API:** `GET /v1/ops/summary?window=24h|7d|30d` returns every panel's numbers in one response,
  bucketed by hour (24h) or day (7d, 30d) in UTC. `GET /v1/ops/failures?limit=50&before=` lists
  recent failed or stopped jobs.
- **Panels:**
  1. **Now:** queued and running jobs, age of the oldest queued job, jobs with an expired lease,
     the last time a worker claimed or renewed a job, and datasets waiting for review.
  2. **Outcomes:** runs by result over time (answered, abstained, budget stop, iteration limit,
     model error, failed job), answer rate, abstain rate, retries.
  3. **Latency:** p50 and p95 queue wait and run time, from new `jobs.started_at` and
     `jobs.finished_at` columns set on claim and on finish (older rows have none and are left out).
  4. **Model calls:** calls, p50 and p95 latency, tokens, and cached-token share per provider and
     model; failed attempts by kind (rate limited, transient, permanent, invalid tool call) over
     time.
  5. **Cost:** spend over time, by model, and the top workspaces; each workspace's spend today
     against its daily budget; average cost per answered run.
  6. **Quality signals:** verify checks that failed and runs that needed a repair (from `verify`
     events, counted only: problem texts quote the data), failed tool calls, and failed dataset
     builds. Eval scores stay offline in `docs/eval/`.
  7. **Sign-ins:** refused keys, links, and sessions over time, and sign-ins. Rate-limit refusals
     are not stored, so they are not shown.
  8. **Recent failures:** time, workspace, kind, attempt, stop reason, the first 300 characters of
     the worker's error (a run's stop detail is left out, since it quotes the data), the run id,
     and trace links when `DA_AGENT_TRACE_UI_URL` is set.
- **Failed model calls** are written to `llm_calls` with a new `error_kind` column and zero cost,
  in the same place the client already records successful calls, so retries inside a run become
  visible.
- The dashboard shows ids, names, counts, and error classes. It does not show questions, answers,
  or cell values: those stay in the workspace and in Phoenix.
- Outcome colours are the dataviz status palette, validated pairwise; every chart has a legend
  and a table or tiles with the same numbers, so colour never carries meaning alone.

**Consequences:**
- Aggregates run on the live tables. That is enough for a self-hosted install; a large one would
  add rollup tables or a Prometheus endpoint without changing the page.
- Two migrations: job timing columns, and `llm_calls.error_kind`. Old rows still display.
- Tests: each aggregate on fixed rows in Postgres, the 404 for members and keys, and the empty
  state of a fresh install. The page itself gets component tests.

**Rejected:**
- Prometheus and Grafana: the standard for infrastructure metrics, but two more services, a
  second login, and metrics that the database already holds.
- A command-line `admin stats`: cheap, but not something to watch.
- Showing questions in the failure list: helps debugging, but the operator can open the trace,
  and the dashboard then never holds user data.
