# 0017: Job leases on the database clock, and plain SQL migrations

**Status:** Accepted, 2026-11-08

**Context:** ADR 0003 chose a jobs table claimed with `FOR UPDATE SKIP LOCKED` and left leases,
heartbeats, and retries to us. The backend also needs its first application tables, so it needs a
way to create and change them.

**Decision:**
- A claim is one `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)`. It takes the
  oldest due `queued` job, or a `running` job whose lease ran out, if attempts remain, and sets
  `worker_id`, `attempts + 1`, and `lease_until = now() + lease`.
- Leases use the database clock (`now()`), so worker clocks never matter. A worker renews its lease
  by heartbeat. `heartbeat`, `complete`, and `fail` match on `worker_id` and `status = 'running'`,
  so a worker that lost its job cannot overwrite the result of the worker that took it over.
- `fail` with a delay queues the job again after it, unless the last attempt is used.
  `fail_expired` ends jobs whose lease ran out on the last attempt, so a job that keeps killing its
  worker stops.
- Reads take a `workspace_id` and return nothing for another workspace's job.
- Migrations are SQL files in `src/da_agent/storage/migrations/`, applied in name order in one
  transaction under an advisory lock and recorded in `schema_migrations`.

**Consequences:**
- Delivery is at least once: a job can run twice when a lease runs out under a slow but live
  worker. Run jobs resume from their LangGraph checkpoint, keyed by job id, so a second run repeats
  at most one model turn, and tools only read.
- Tests run on a real Postgres, each in its own schema. One holds a row lock in a second
  connection and proves a claim skips the job instead of waiting; without `SKIP LOCKED` it blocks.
  Without it the queue is still correct, because Postgres rechecks a locked row, only slower.
- No down migrations: a mistake is fixed by a new forward migration.

**Rejected:**
- Alembic with SQLAlchemy: two dependencies and a model layer for a handful of tables written in
  SQL anyway.
- Worker clocks for leases: skew between machines would expire leases early or late.
- `LISTEN/NOTIFY` for waking workers: polling every second is enough at this scale, and can be added
  later without changing the table.
