# 0044: SLOs and alerts with Prometheus metrics from the processes and Alertmanager

**Status:** Accepted, 2026-11-21. Design: [SLOs and alerts](../design/2026-11-21-slo-alerts.md).

**Context:** The ops dashboard computed its panels from Postgres when someone opened it. Nothing
watched the system between visits: no SLOs, no error budgets, no alerts.

**Decision:**
- **Metrics where the work happens** (`prometheus_client`, `da_agent.telemetry.metrics`). The worker
  counts finished runs by outcome (the ops panel's groups), job attempts by result, run and queue
  wait histograms, model calls by route and result with latency, tokens, and cost, and an open
  breaker gauge. The API counts `/v1/` requests by route template, method (unknown methods share
  "other"), and status; health probes of `/openapi.json` are left out, since they would outnumber
  real requests and hide their errors. Every label value a counter can take is created at zero
  at start-up, because a series first created by its own event starts at 1 and `rate()` never sees
  that event. Each process serves `/metrics` on port 9100, apart from the web app and never
  published to the host. Recording a metric never fails the work it measures, and runs whose
  worker died on their last attempt are counted by the worker that ends them.
- **Metrics read from Postgres only for state that lives there** (`da_agent.telemetry.db_metrics`,
  in the API): queue depth, the oldest due job, each workspace's share of its daily budget, and
  ratings in the last 7 days. Read at most every 30 s, on a connection that gives up after 3 s;
  a scrape never waits for a read in progress, and when Postgres cannot be read the last values
  stay and `da_agent_db_metrics_up` drops to 0. A workspace with a zero budget that spent counts as
  over budget. Run outcomes and latency do not come from
  the database, so those alerts still work when it fails.
- **SLOs** over 7 days, as recorded series in `deploy/prometheus/rules/slo.yml`: runs 99% without a
  failure or model error (cancelled, budget, and deadline stops are not failures), run attempts
  95% within 60 s, first attempts 99% started within 30 s of queueing, API 99.5% without 5xx.
- **Alerts** (`alerts.yml`): multiwindow burn rates from the Google SRE workbook, "page" at 14.4x
  over 1 h and 5 m, "ticket" at 6x over 6 h and 30 m, each needing a minimum number of events so a
  quiet hour does not page on one bad run. Threshold alerts: WorkerDown, ApiDown, QueueStuck,
  ModelErrorsHigh, MetricsDatabaseUnreadable, BudgetNearlyUsed (80%), FeedbackLow (under 70% good
  with at least 20 ratings). Rules are tested with `promtool test rules`, including the quiet-hour
  case, from pytest, with the images compose.yml pins.
- **Alertmanager** groups by alert name and severity, inhibits QueueStuck and queue burns while
  WorkerDown fires, and posts to `DA_AGENT_ALERT_WEBHOOK_URL` with resolved notices; unset, alerts
  stay in Alertmanager. The URL goes to a file inside the container, not into config or logs.
- **The ops dashboard** lists firing alerts from Alertmanager (`GET /v1/ops/alerts`, operators
  only), pages first; an unreachable Alertmanager shows as unavailable, never as an API error.
- Prometheus v3.15.1 and Alertmanager v0.35.0, pinned by digest; their UIs on 127.0.0.1 only.
  Alertmanager runs as one instance with clustering off, so it has no gossip port.

**Rejected:**
- An SLO engine inside the app (Postgres to SLIs to an alert table): it reimplements Alertmanager's
  grouping, deduplication, silences, and routing, and fails together with Postgres.
- All metrics computed from Postgres on scrape: monitoring would go dark with the database, and
  queries over whole tables grow with them. Kept only for state that lives there.
- Grafana for now: Prometheus and the ops dashboard are enough; it can be added later.

**Consequences:**
- Follow-ups (2026-11-21): ModelErrorsHigh leaves calls our own limiter refused out; no
  `_created` series; a failed attempt is counted with the status the queue gave the job, so a
  failure after a cancel request counts as cancelled; malformed Alertmanager items are skipped and
  each alert shows its state and labels; an automated test checks that WorkerDown inhibits
  QueueStuck.
- Process counters reset on restart; `rate()` and `increase()` account for it.
- With little traffic, burn-rate alerts need their minimum event counts; a real outage on a quiet
  hour may only reach the ticket alert, or WorkerDown and QueueStuck.
- Checked for real on 2026-11-21: with the worker stopped, WorkerDown reached a test webhook after
  about 3 minutes, and the resolved notice about 5 minutes after it came back.
- Two more containers and their volumes (15 days of metrics).
- Prometheus and Alertmanager sit on the edge network too, which publishing a port needs. There,
  the web and Phoenix containers can reach their APIs and every `:9100` endpoint, none with
  authentication: anything inside the Compose networks can read metrics or silence alerts.
- 14.4 and 6 are the workbook factors for a 30-day budget; over 7 days a page means about 8.6% of
  the budget per hour. At low traffic the 5-minute window is often empty, so a page can resolve
  and fire again around a lone failure.
- Queue wait covers every job kind, and streaming responses are timed to their headers; both are
  kept simple on purpose and can be split later.
