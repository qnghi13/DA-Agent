# Threat model

**Status: mostly design.** Controls are planned unless noted. A control counts as done only when a
test proves it. Done so far: the SQL guard and hidden columns on SQLite (`tests/security/`), keeping
answers away from the model during eval runs (`tests/unit/test_datamate_eval.py`), and the sandbox
limits in the first two rows below (`tests/security/test_sandbox_escape.py`, `tests/unit/test_runner.py`),
and the DuckDB SQL path: guard, engine settings, and a sandbox per query, each tested without the
layers before it (`tests/security/test_sql_boundary.py`, `test_duckdb_engine.py`, `test_sandbox_sql.py`).

## Assets

- Uploaded datasets, which can contain personal data.
- Provider API keys and workspace API keys.
- The host machine that runs Docker.
- Money: LLM spend.
- Eval integrity: `train/answers.jsonl` and holdout shops.

## Attackers

- A user uploading a malicious file or writing a malicious question.
- Third-party content inside a file, such as a cell written to manipulate the model.
- The LLM itself, which can produce harmful code or SQL by mistake or after injection.
- A workspace trying to reach another workspace's data.

Out of scope for now: a malicious operator, a compromised host, and supply chain attacks beyond
pinning dependencies with `uv.lock`.

## Trust boundaries

```
Untrusted:  browser input, uploaded files, cell values, model output, sandbox output
─────────────────────────── boundary: validation, schemas, framing as data
Trusted:    API, Worker, Runner code, Postgres
─────────────────────────── boundary: Runner + container limits
Untrusted:  anything running inside the sandbox
```

Injection is assumed to succeed sometimes. Permissions, not prompts, must limit the damage.

## Threats and controls

| Threat | Control | Proof |
|---|---|---|
| LLM-generated code or a malicious upload attacks the host or network | Container with `--network none`, read-only, non-root, `--cap-drop ALL`, `no-new-privileges`, RAM, CPU, PID, and time limits, no secrets. Uploads, including files added to a dataset, are parsed here too ([ADR 0009](adr/0009-parse-uploads-in-sandbox.md), [ADR 0045](adr/0045-dataset-versions.md)) | **Done:** tests attempt network, file writes, env reads, other datasets, the Docker socket, fork bomb, memory exhaustion, endless loops, output floods |
| Runner compromised through its Docker socket | Only the Runner holds the socket. It accepts typed requests and builds container options itself, so callers cannot set mounts or flags. Deployed, it is its own service: a bearer token from the worker, no provider keys, no database URL, no route out, and Python runs not exposed ([ADR 0024](adr/0024-runner-service.md)) | **Done:** request fields cannot change mounts or flags, dataset ids cannot name paths or follow symlinks out; service tests refuse missing tokens, extra fields, and outputs outside staging; volume mounts show only the dataset's subpath. Checked on the running Compose stack: the worker has no socket and no Docker client, the runner cannot reach the internet |
| SQL reads files, writes, or attaches databases | sqlglot allows only `SELECT` on plain table names, with no file, settings, or environment functions. DuckDB opens read-only with `enable_external_access` off and its configuration locked, in a new sandbox per query with only that dataset mounted and a timeout ([ADR 0011](adr/0011-sql-per-request-sandbox.md)) | Tests attempt `COPY`, `read_csv`, `ATTACH`, file paths as tables, `INSTALL`, `SET`, and another dataset, with and without the guard |
| Prompt injection from cells or descriptions | File content is always framed as data: cells reach the model only as JSON tool results, and document text cannot open or close its `<document>` frame. Tools have least privilege, and `verify` withholds numbers no query returned | **Done:** `tests/security/test_injection.py` assumes the injection works. A scripted model obeys planted text and tries hidden columns, writes, `ATTACH`, file reads, another dataset, and an injected number; each fails, and the planted secret never reaches the model or the traces |
| Memory poisoning: an injected or wrong entry shapes later runs | Only people write memory through the API; agent suggestions stay `proposed` until a member approves them; entries are framed as data, audited, and scoped to one workspace and dataset (ADR 0030) | **Done:** `tests/integration/test_memory.py` and `test_api_memory.py` try another workspace on every memory and conversation route; a note cannot forge a section or close its frame (`tests/unit/test_memory_documents.py`) |
| Malicious spreadsheet: zip bomb, external entities, huge sheets | Parsing runs in the sandbox. Size, compression ratio, row, column, and cell limits. `defusedxml` guards openpyxl. Macros are ignored | **Done:** zip bomb, oversized, broken, and unreadable file tests (`tests/unit/test_ingestion_raw.py`) |
| Sandbox output tricks the Runner: symlinks in `/out`, huge output | The Runner refuses links and special files, caps the output size, and opens the report with `O_NOFOLLOW` | **Done:** `tests/unit/test_runner.py` |
| Path traversal through upload names | Stored names keep only the last path part, in ASCII letters, digits, `_` and `-`, with an allowed extension. Original names are kept only as metadata. `POST /v1/datasets/{id}/files` uses the same `storage_name`, file count over the whole new version, size limits, and job limits, and links earlier files only from the dataset's own folder | **Done:** `tests/unit/test_uploads.py`, and an API test uploading `../../../escape.csv` |
| Cross-workspace access | Every query carries `workspace_id`, and anything outside the caller's workspace is "not found". Run jobs open only ready datasets of their own workspace. One DuckDB file per dataset. The sandbox mounts one dataset | **Done:** API, queue, and dataset tests across two workspaces (`-m security`), including the run list; dropping the workspace filter fails them |
| Rate limits bypassed with a forged `X-Forwarded-For` | In Compose the API is reachable only through nginx, which replaces the header with the real client address | Config review (`web/nginx.conf`); the API publishes no port |
| Stolen or guessed API key | 256-bit keys stored as SHA-256 hashes, compared in constant time, revocable. Failed attempts are logged with the key prefix only. Rate limits per key and per address | **Done:** API tests for missing, wrong, and revoked keys, hash-only storage, audit rows ([ADR 0020](adr/0020-http-api.md)), request limits per key, and wrong keys per address blocking even a valid key until the window ends ([ADR 0022](adr/0022-rate-and-money-limits.md)) |
| Stolen or forwarded login link | One-time token in the URL fragment, so it never reaches server logs or `Referer`; 256 bits stored as a hash; valid 15 minutes; spent atomically on first use. Failed redemptions count towards the per-address limit ([ADR 0025](adr/0025-browser-sessions-and-login-links.md)) | **Done:** tests for reuse, expiry, guessed tokens slowed and audited, hash-only storage |
| Stolen session cookie, or a cross-site request riding it | `HttpOnly`, `SameSite=Strict`, `Secure` with the `__Host-` prefix on https; a cookie request that changes anything, and every sign-in, must carry our `Origin`. Sessions expire after 30 idle days and 90 days in all, and are revocable one by one or per workspace | **Done:** API tests for missing and foreign `Origin` (including sign-in from a foreign page), logout, revocation, and cookie flags on http and https |
| A member or key reaches the operator view, or the operator reaches workspace data | Roles are separate: the operator has no workspace, so workspace endpoints are "not found" for it; `/v1/ops` is "not found" for members and keys. The dashboard returns counts, timings, money, ids, and names, never questions, answers, verify problems, or SQL errors ([ADR 0026](adr/0026-operator-dashboard.md)) | **Done:** role tests both ways, invite links only for the caller's own role, and dashboard tests that seeded user text never appears |
| Cost runaway | Token and money limits per run, day, and workspace, from a ledger in Postgres that survives retries. A breach stops the run cleanly; the API refuses new jobs until midnight UTC | **Done:** budget stop tests in the graph and the worker, a ledger read by a second worker, and API 429s ([ADR 0022](adr/0022-rate-and-money-limits.md)) |
| Secrets in the agent manifest, which is stored in Postgres and `metrics.json` | Routes contribute only provider, model, and request options; nothing is read from the environment but `DA_AGENT_GIT_SHA` ([ADR 0038](adr/0038-run-manifest.md)) | **Done:** `tests/unit/test_manifest.py` and `tests/integration/test_entrypoints.py` check that an API key never appears |
| Personal data in prompts, logs, traces | Sensitive columns masked. Columns marked hidden do not appear in views | **Partly done:** a hidden-column canary never reaches the model, steps, or spans (`test_injection.py`). Logs and the UI come later |
| XSS from cell values or model output | Treat as text in React. Escape everything in HTML reports, with a strict Content Security Policy in the page and the response header. The UI shows reports in an iframe sandboxed without `allow-same-origin`, so report scripts cannot call the API; the session cookie is `HttpOnly` | **Done for reports** ([ADR 0016](adr/0016-html-reports.md)): script payloads in cells, titles, and questions. The UI renders model text with React only; its nginx sends `frame-ancestors 'none'` but no `script-src` yet, because the report iframe inherits the page policy |
| Data exfiltration through a chart | Code builds every Vega-Lite spec from a query result; the model only names a mark and columns, so a spec has inline data only ([ADR 0015](adr/0015-charts-built-by-code.md)) | **Done:** a model-sent `data.url`, transform, and HTML title never reach the spec |
| Data exfiltration through rendered answers | Do not load remote images or links automatically from model output | UI test |
| CSV or spreadsheet formula injection on export | Text cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading `'`; numbers are unchanged ([ADR 0032](adr/0032-result-export.md)) | **Done:** `tests/unit/test_csv_export.py` |
| Result export used to run arbitrary SQL or read another workspace's data | The export endpoint takes a run and a query index, never SQL; the worker re-checks the stored SQL with the guard; the sandbox gets the one dataset read-only and an empty `/out`, with DuckDB locked as for queries; files are served only to their workspace, as attachments with `nosniff`, for 24 hours ([ADR 0032](adr/0032-result-export.md)) | **Done:** `tests/security/test_sandbox_export.py` tries `COPY` into `/out`, file reads, `ATTACH`, and another dataset in a real container; API tests send SQL in the body and try another workspace's export; worker tests show a rejected SQL never runs and sandbox stderr never reaches the user |
| SQL typed by a person (typo, plain text, `DROP`, several statements, file reads, another dataset) | The API runs the SQL guard before queuing and answers 422 with its message; the worker checks again; the sandbox gets only the run's dataset, read-only, with DuckDB locked; the run and dataset must belong to the caller's workspace; the audit log keeps the SQL's length, not its text ([ADR 0033](adr/0033-edited-sql-as-a-query-job.md)) | **Done:** `tests/integration/test_api_queries.py` sends typos, Vietnamese text, `DROP`, two statements, and `read_csv`, and tries another workspace; `tests/integration/test_worker_queries.py` shows forbidden SQL and another dataset never reach the sandbox |
| Column profile exposes data or hidden columns | The profile is built in the sandbox from the clean table, which has no hidden columns; it keeps at most five values per text column, cut to 60 characters, in the dataset summary, served only to the dataset's workspace; the agent's prompt does not read it ([ADR 0034](adr/0034-column-profile-at-build.md)) | **Done:** `tests/unit/test_dataset_build.py` (profile fields, value cut), `tests/integration/test_datasets.py` (summary) |
| A deleted dataset's data stays behind (rows, files, checkpoints, a job that keeps reading it) | `DELETE /v1/datasets/{id}` marks the dataset `deleting` in one transaction: every route answers "not found" for it and its jobs, enqueuing locks the dataset row so no job starts on it, and its jobs are cancelled. A background `delete` job waits for running jobs, then removes export, upload, and build files, checkpoint threads, and rows, in an order that makes a retry safe. Spend (`llm_calls`) and audit rows are kept on purpose; they hold no data values ([ADR 0050](adr/0050-dataset-deletion.md)) | **Done:** Postgres tests check the whole inventory is gone, a retry after a crash at each step, a job finishing during the delete, 404 on every route, and another workspace untouched |
| Eval answer leakage | `answers.jsonl` is never mounted. Only `eval/scorer` reads it, in a separate process | Test that sandbox mounts exclude `train/` root |

## Residual risks and open items

- **Runc is not a hard boundary.** A kernel or container escape reaches the host. gVisor stays
  optional, and the README must state this limit plainly.
- **The Docker socket equals root on the host.** A Runner compromise is a host compromise. The Runner
  stays small for this reason, and runs as root in its container because the socket already gives
  root. In local development without `DA_AGENT_RUNNER_URL`, the worker starts sandboxes itself and
  so holds the socket; that mode is not for deployment.
- **Parser exploits.** Decided in [ADR 0009](adr/0009-parse-uploads-in-sandbox.md): parsing runs in
  the sandbox. A parser bug then reaches only the sandbox, unless it is combined with a kernel escape.
- **Data sent to LLM providers.** Column profiles, sample values, and query results leave the machine.
  Free tiers may use them under different terms. During development, send only demo or DataMate data.
  The README must warn self-hosters and point to local models.
- **Phoenix has no workspace isolation.** Anyone who can open it reads every workspace's traces,
  including prompts with cell values. Phoenix stays on `127.0.0.1`, and `DA_AGENT_TRACE_UI_URL`,
  which puts trace links in API responses, is for operators only and stays unset on a shared or
  public deployment ([ADR 0023](adr/0023-trace-links-and-run-list.md)).
- **Successful injection can still produce a wrong answer.** Verification reduces this but cannot
  eliminate it.
- **Deleting a dataset does not delete Phoenix traces.** Traces hold prompts and query results and
  expire with Phoenix's own retention ([ADR 0050](adr/0050-dataset-deletion.md)). Container logs are
  the same.
- **Golden rerun reports are not deleted.** `data_dir/goldens/goldens-<time>.jsonl`, written by
  the operator's `python -m da_agent.goldens run`, mixes datasets and nothing in the database points
  to it. The operator owns those files; the golden cases themselves are deleted with the dataset.
