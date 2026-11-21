# SLOs and alerts with Prometheus and Alertmanager

**Status:** approved 2026-11-21. The user chose metrics instrumented in code, plus a few gauges
read from Postgres, `prometheus_client`, and a generic webhook. **ADR:**
[0044](../adr/0044-slo-alerts.md).

## Why

This is step 6, the last step of the harness track. The ops dashboard computes its panels from
Postgres when someone opens it. Nothing watches the system between visits. There are no SLOs and
no alerts.

## Goals and non-goals

**Goals**

- **Service-level objectives (SLOs)**, each with an error budget over 7 days:
  - *Run success:* 99% of finished runs do not end `failed` or with a model error. Cancelled
    runs, budget stops, and deadline stops are not failures.
  - *Run latency:* 95% of runs finish an attempt within 60 s.
  - *Queue wait:* 99% of first attempts start within 30 s of being queued.
  - *API availability:* 99.5% of API requests do not return a 5xx.
- **Alerts.** Each SLO gets multiwindow burn-rate alerts. There are two severities:
  - *page:* burn rate 14.4 over 1 h and 5 m;
  - *ticket:* burn rate 6 over 6 h and 30 m.

  Each alert fires only when the window has a minimum number of events, because low traffic would
  otherwise page on one bad run. There are also threshold alerts:
  - *WorkerDown:* no worker target is up for 2 m.
  - *QueueStuck:* the oldest queued job is more than 5 m old for 5 m. WorkerDown inhibits it.
  - *ModelErrorsHigh:* more than 20% of LLM calls fail for 10 m.
  - *BudgetNearlyUsed:* a workspace has used 80% of its daily model budget.
  - *FeedbackLow:* the 👍 rate is under 70% over 7 days, with at least 20 ratings.
- **Delivery.** Alertmanager groups and routes alerts to a webhook set in `.env`
  (`DA_AGENT_ALERT_WEBHOOK_URL`, which works with Slack-compatible or custom receivers). Without
  it, alerts stay in Alertmanager and on the ops dashboard. Resolved alerts are sent too.
- **Ops dashboard.** The operator view gets an "Alerts" section, read through the API from
  Alertmanager.

**Non-goals**

- Grafana. Prometheus and the ops dashboard are enough for now, and it can be added later.
- Paging services such as PagerDuty.
- Long-term metric storage. Prometheus keeps 15 days.
- Tracing changes.
- Per-workspace SLOs.

**Anti-goals**

- Metrics endpoints reachable from outside the Compose networks.
- Workspace names, questions, or any user text in metric labels. Only workspace ids may appear,
  and only on the budget gauge.
- Monitoring that fails together with Postgres. The success and latency SLIs come from process
  counters, not from database queries.

## Approaches

| Option | Trade-off | Decision |
|---|---|---|
| A. An SLO engine inside the app (Postgres to SLIs to its own alert table) | No new infrastructure, but it reimplements Alertmanager (grouping, dedupe, silences, routing) and fails together with Postgres | Rejected |
| B. Instrument in code (`prometheus_client`), Prometheus scrapes, rules in PromQL, Alertmanager routes | The industry norm: RED metrics, cheap to scrape, independent of the database. Counters reset on restart (`rate()` handles it). Adds two containers | **Chosen for SLIs** (user, 2026-11-21) |
| C. Metrics computed from Postgres on scrape | One source of truth, but it goes dark when Postgres does, and costs grow with table size | **Chosen only for state that lives in the database:** queue depth, the oldest queued job, budget use, feedback counts (read at most every 30 s) |

## Design

**Where metrics are served.** Each process serves `/metrics` on its own port, 9100, through
`prometheus_client.start_http_server`. That port is outside the FastAPI app and the web proxy.
Prometheus is on the internal `backend` network only. Alertmanager is on `backend` and `edge`,
so it can reach the webhook. Their user interfaces listen on `127.0.0.1:9090` and
`127.0.0.1:9093`.

**Worker metrics** (in `da_agent.telemetry.metrics`):

| Metric | Labels | Meaning |
|---|---|---|
| `da_agent_runs_total` | `outcome` | the same groups as the ops panel: answered, abstained, stopped, model_error, failed, cancelled |
| `da_agent_job_attempts_total` | `kind`, `result` | `result` is one of succeeded, retry, failed, cancelled, lost |
| `da_agent_run_duration_seconds` | | histogram with buckets 1, 2, 5, 10, 20, 30, 60, 120, 300 |
| `da_agent_queue_wait_seconds` | | first attempts only; needs `created_at` from `claim` |
| `da_agent_llm_calls_total` | `provider`, `model`, `result` | `result` is ok or an error kind |
| `da_agent_llm_call_duration_seconds` | | histogram |
| `da_agent_llm_tokens_total` | `direction` | |
| `da_agent_llm_cost_usd_total` | | |
| `da_agent_breaker_open` | `route` | gauge |

**API metrics:**

- `da_agent_http_requests_total{route, method, status}` and
  `da_agent_http_request_duration_seconds{route}`. `route` is the route's path template, never
  the raw path, so it does not grow with every id.
- A collector reads these from Postgres, cached for 30 s:
  - `da_agent_queue_jobs{status}`
  - `da_agent_queue_oldest_seconds`
  - `da_agent_budget_used_ratio{workspace_id}`
  - `da_agent_feedback_total{rating}`

**Rules.** They live in `deploy/prometheus/rules/`:
- recording rules for each SLI over 5m, 30m, 1h, 6h, and 7d;
- alerting rules as above, each with `severity`, `slo`, and a `summary` annotation.

They are tested with `promtool test rules`, run in Docker by an integration test.

**Alertmanager.**
- Route grouping is by `alertname`, with `group_wait` 30 s, `group_interval` 5 m, and
  `repeat_interval` 4 h.
- WorkerDown inhibits QueueStuck and the queue-wait alerts.
- The webhook URL comes from `.env` through a file. When it is unset, alerts go to a null
  receiver.

**API.** `GET /v1/ops/alerts` is operator-only. It reads Alertmanager's `/api/v2/alerts` (the URL
is in `DA_AGENT_ALERTMANAGER_URL`) and returns each alert's name, severity, summary, start time,
and state. If Alertmanager is unreachable, it returns an empty list with a flag, never a 500.

## Left to the builder

- Exact image versions. They must be pinned by digest, like Postgres.
- How the webhook URL reaches Alertmanager (`url_file` from a Compose `configs` entry, or an
  entrypoint that writes it).
- How the alerts section looks on the dashboard.
