# SLOs and alerts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Prometheus metrics to the worker and the API, write SLO and alert rules in PromQL,
route alerts through Alertmanager to a webhook, and show active alerts on the ops dashboard.

**Architecture:**
- The SLIs come from `prometheus_client` counters and histograms in each process, served on
  port 9100.
- Queue, budget, and feedback state is read from Postgres on scrape, cached for 30 s.
- Prometheus and Alertmanager run as Compose services.
- The rules are tested with `promtool`.

**Tech Stack:** Python 3.12, prometheus_client, Prometheus, Alertmanager, Docker Compose,
FastAPI, React.

**Spec:** [2026-11-21-slo-alerts.md](2026-11-21-slo-alerts.md)

## Global Constraints

- **One new Python dependency:** `prometheus_client`. The user approved it on 2026-11-21.
- **Image pins:** images are pinned by version and digest.
- **Metric labels:** no user text in labels. Workspace ids appear only on the budget gauge. The
  `route` label is a path template.
- **Ports:** 9100 is never published to the host. The Prometheus and Alertmanager UIs bind to
  127.0.0.1 only.
- **Tests:**
  - Each test uses its own `CollectorRegistry`; no test reads the global registry.
  - Unit tests make no network calls.
  - `promtool` tests run only when Docker is available.

## Review Focus

1. **Metric collection must never break a job.** When a metric fails to record, the job still
   completes and the run still counts.
2. **Low traffic.** One failed run out of two in an hour must not page.
3. **Postgres down.** The DB collector returns its last values, or nothing. Scrapes still
   succeed and still serve the process counters.
4. **Worker replicas.** Prometheus finds every worker. WorkerDown fires only when none is up.
5. **No webhook.** When `DA_AGENT_ALERT_WEBHOOK_URL` is unset, Alertmanager still starts and
   keeps alerts without sending them.

---

### Task 1: Metrics module and worker instrumentation

**Files:**
- Create: `src/da_agent/telemetry/metrics.py`
- Modify: `worker/worker.py`, `worker/__main__.py`, `llm/client.py` (call metrics), `storage/jobs.py` (`claim` returns `created_at`)
- Test: `tests/unit/test_metrics.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Produces `WorkerMetrics(registry)`, with methods:
  - `run_finished(outcome)`
  - `attempt(kind, result)`
  - `run_duration(seconds)`
  - `queue_wait(seconds)`
- Produces `LLMMetrics(registry)`, which `LLMClient` takes as `metrics=`.
- Produces `run_outcome(result_json) -> str`, the same grouping as the ops panel's `_OUTCOME`.
- Produces `serve(port, registry)`.

**Steps:**
- [ ] Write the tests:
  - The outcome grouping matches the ops `_OUTCOME` for every stop reason.
  - One worker job of each result (succeeded, retryable, permanent, cancelled, lost) increments
    the right counters.
  - The queue wait is recorded for first attempts only.
  - The LLM call counter and histogram are recorded for successes and for each error kind.
  - A metrics method that raises does not fail the job.
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass.

### Task 2: API metrics and the Postgres collector

**Files:**
- Modify: `src/da_agent/api/app.py` (middleware), `api/__main__.py` (serve 9100)
- Create: `src/da_agent/telemetry/db_metrics.py`
- Test: `tests/integration/test_metrics_api.py`

**Steps:**
- [ ] Write the tests:
  - Request counts are labelled by route template, for example `/v1/runs/{run_id}`, with
    method and status.
  - The collector reports queue jobs by status, the age of the oldest queued job, budget ratios
    per workspace, and feedback counts.
  - It caches its values for 30 s.
  - When Postgres is unreachable, it keeps its last values, and the scrape still succeeds.
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass.

### Task 3: Prometheus rules and promtool tests

**Files:**
- Create: `deploy/prometheus/prometheus.yml`, `deploy/prometheus/rules/slo.yml`, `deploy/prometheus/rules/alerts.yml`, `deploy/prometheus/tests/*.yml`
- Test: `tests/integration/test_prometheus_rules.py`, which runs `promtool check config` and `promtool test rules` in Docker

**Steps:**
- [ ] Write the promtool tests first:
  - A run error ratio of 20% over 1 h with enough runs fires `RunSuccessBudgetBurn` with
    severity page.
  - One failure out of two runs does not fire.
  - Latency over 60 s fires the latency burn alert.
  - With worker `up` at 0 for 2 m, WorkerDown fires.
  - QueueStuck fires as a rule. Its inhibition is checked in Task 4.
  - BudgetNearlyUsed fires at a ratio of 0.85.
  - FeedbackLow fires with 25 ratings and a 👍 rate of 60%.
- [ ] Write the rules.
- [ ] Run the tests: they pass.

### Task 4: Compose services, Alertmanager, and the webhook

**Files:**
- Modify: `compose.yml`, `.env.example`
- Create: `deploy/alertmanager/alertmanager.yml` (and the webhook file mechanism)

**Steps:**
- [ ] Add the `prometheus` and `alertmanager` services, pinned. Scrape `api:9100`, and find
  workers through `dns_sd_configs` for `worker`.
- [ ] Check the Alertmanager config with `amtool check-config`.
- [ ] Check the inhibition: QueueStuck is muted while WorkerDown fires. An integration check
  posts alerts with `amtool` or the API and reads them back.
- [ ] Run `docker compose config -q`. Then `up -d`, and confirm that every Prometheus target is
  up through the 127.0.0.1:9090 API.

### Task 5: Alerts on the ops dashboard

**Files:**
- Modify: `api/app.py` (`GET /v1/ops/alerts`), `config.py` (`DA_AGENT_ALERTMANAGER_URL`), `web/src/ops.ts` and `OpsDashboard.tsx`
- Test: `tests/integration/test_api.py` with a fake Alertmanager (an `httpx2` mock transport), and `web/src/ops.test.ts`

**Steps:**
- [ ] Write the tests:
  - The endpoint is operator-only.
  - It maps fields from Alertmanager v2.
  - When Alertmanager is unreachable, it returns `{"alerts": [], "available": false}`.
  - In the UI, firing alerts sort by severity.
- [ ] Implement, then run the tests.

### Task 6: Real check and docs

- [ ] **Real check, at $0:**
  1. Point the webhook at a throwaway receiver container on the edge network.
  2. Stop the worker.
  3. Within about 3 minutes, WorkerDown arrives at the receiver.
  4. Start the worker again, and the resolved alert arrives.
  5. Remove the receiver.
- [ ] Write ADR 0044, and learning note 23 in Vietnamese.
- [ ] Update `architecture.md` (the observability section), the README (where Prometheus and
  Alertmanager are and how to set the webhook), `progress.md`, and `.env.example`.

### Final

- Run `uv run --env-file .env pytest`, ruff, `npm test`, and `tsc`.
- Run `docker compose config -q`.
- Get a fresh whole-branch review on the most capable model.
