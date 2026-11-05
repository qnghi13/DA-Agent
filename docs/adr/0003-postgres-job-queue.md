# 0003: Job queue on Postgres

**Status:** Accepted, 2026-11-05

**Context:** Ingestion and runs are long-running and must survive restarts. Postgres is already
required for checkpoints.

**Decision:** Use a jobs table claimed with `FOR UPDATE SKIP LOCKED`.

**Consequences:**
- Fewer services, which suits self-hosting and limited RAM.
- Job and state changes can share a transaction.
- We write leases, heartbeats, and retry logic ourselves.
- Throughput is lower than a dedicated broker, which is fine at this scale.

**Rejected:**
- Redis with Celery or RQ: one more service and a second source of job state.
