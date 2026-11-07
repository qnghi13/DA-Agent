# 0020: The HTTP API: bearer keys, uploads by safe name, and "not found" across workspaces

**Status:** Accepted, 2026-11-08

**Context:** The plan has no sign-up: a workspace is reached with an API key. The API must accept
untrusted files, start long work without doing it, and keep workspaces apart.

**Decision:**
- Keys look like `dak_<12 hex>_<43 url-safe chars>` and are shown once. The table keeps the prefix,
  to find the row, and a SHA-256 hash of the whole key, compared with `hmac.compare_digest`, also
  when no row matches. A key has 256 random bits, so a slow password hash adds nothing.
- Failed authentication writes an audit row with the request path and, only if the key is
  well-formed, its prefix. Uploads, approvals, and new runs are audited in the same transaction as
  the change.
- Uploads are copied in 1 MB chunks under `uploads_root/<dataset id>/`, with limits on the number
  and size of files. Stored names keep only the last path part in plain ASCII, with an extension
  the ingestion reads; the original name is metadata. The dataset row, its ingest job, and the
  audit row are written in one transaction, and the folder is removed if anything fails.
- Endpoints: `POST /v1/datasets`, `GET /v1/datasets[/{id}]`, `POST /v1/datasets/{id}/approve`,
  `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/report`. Each looks rows up with the
  caller's workspace. A row of another workspace, a malformed id, and a job that is not a run all
  answer 404, so ids reveal nothing.
- Handlers are synchronous and open one connection per request. The report is served with its
  Content Security Policy as a header too.

**Consequences:**
- API tests on Postgres cover keys, uploads (including `../` names, wrong types, and size limits),
  review, runs, reports, the audit log, and isolation. Removing the workspace filter from the
  dataset query fails the isolation tests.
- A connection per request costs a few milliseconds. A pool (`psycopg-pool`) can replace it
  without changing handlers.
- Rate limits, SSE progress, and the `python -m` entry points come next.

**Rejected:**
- 403 for another workspace's rows: it confirms that the id exists.
- Generated storage names such as `001.csv`: safe, but ingestion names tables after files, so the
  user would see meaningless table names.
- bcrypt or Argon2 for keys: built for low-entropy passwords, and slow on every request.
