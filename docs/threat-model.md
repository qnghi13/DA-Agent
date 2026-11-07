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
| LLM-generated code or a malicious upload attacks the host or network | Container with `--network none`, read-only, non-root, `--cap-drop ALL`, `no-new-privileges`, RAM, CPU, PID, and time limits, no secrets. Uploads are parsed here too ([ADR 0009](adr/0009-parse-uploads-in-sandbox.md)) | **Done:** tests attempt network, file writes, env reads, other datasets, the Docker socket, fork bomb, memory exhaustion, endless loops, output floods |
| Runner compromised through its Docker socket | Only the Runner holds the socket. It accepts typed requests and builds container options itself, so callers cannot set mounts or flags | **Done:** request fields cannot change mounts or flags, dataset ids cannot name paths or follow symlinks out |
| SQL reads files, writes, or attaches databases | sqlglot allows only `SELECT` on plain table names, with no file, settings, or environment functions. DuckDB opens read-only with `enable_external_access` off and its configuration locked, in a new sandbox per query with only that dataset mounted and a timeout ([ADR 0011](adr/0011-sql-per-request-sandbox.md)) | Tests attempt `COPY`, `read_csv`, `ATTACH`, file paths as tables, `INSTALL`, `SET`, and another dataset, with and without the guard |
| Prompt injection from cells or descriptions | File content is always framed as data: cells reach the model only as JSON tool results, and document text cannot open or close its `<document>` frame. Tools have least privilege, and `verify` withholds numbers no query returned | **Done:** `tests/security/test_injection.py` assumes the injection works. A scripted model obeys planted text and tries hidden columns, writes, `ATTACH`, file reads, another dataset, and an injected number; each fails, and the planted secret never reaches the model or the traces |
| Malicious spreadsheet: zip bomb, external entities, huge sheets | Parsing runs in the sandbox. Size, compression ratio, row, column, and cell limits. `defusedxml` guards openpyxl. Macros are ignored | **Done:** zip bomb, oversized, broken, and unreadable file tests (`tests/unit/test_ingestion_raw.py`) |
| Sandbox output tricks the Runner: symlinks in `/out`, huge output | The Runner refuses links and special files, caps the output size, and opens the report with `O_NOFOLLOW` | **Done:** `tests/unit/test_runner.py` |
| Path traversal through upload names | Generated storage names. Original names are kept only as metadata | Unit tests |
| Cross-workspace access | Every query carries `workspace_id`. One DuckDB file per dataset. The sandbox mounts one dataset | Isolation test across two workspaces |
| Stolen or guessed API key | Keys stored hashed, rate limits, audit log | Unit and API tests |
| Cost runaway | Token and money limits per run, day, and workspace. A breach stops the run cleanly | Budget stop test |
| Personal data in prompts, logs, traces | Sensitive columns masked. Columns marked hidden do not appear in views | **Partly done:** a hidden-column canary never reaches the model, steps, or spans (`test_injection.py`). Logs and the UI come later |
| XSS from cell values or model output | Treat as text in React. Escape everything in HTML reports | Tests with script payloads in cells |
| Data exfiltration through a chart | Vega-Lite specs are validated and allow inline data only, with no external URLs | Tests with `data.url` and image marks |
| Data exfiltration through rendered answers | Do not load remote images or links automatically from model output | UI test |
| CSV or spreadsheet formula injection on export | Neutralize cells starting with `=`, `+`, `-`, `@` on export | Unit test |
| Eval answer leakage | `answers.jsonl` is never mounted. Only `eval/scorer` reads it, in a separate process | Test that sandbox mounts exclude `train/` root |

## Residual risks and open items

- **Runc is not a hard boundary.** A kernel or container escape reaches the host. gVisor stays
  optional, and the README must state this limit plainly.
- **The Docker socket equals root on the host.** A Runner compromise is a host compromise. The Runner
  stays small for this reason.
- **Parser exploits.** Decided in [ADR 0009](adr/0009-parse-uploads-in-sandbox.md): parsing runs in
  the sandbox. A parser bug then reaches only the sandbox, unless it is combined with a kernel escape.
- **Data sent to LLM providers.** Column profiles, sample values, and query results leave the machine.
  Free tiers may use them under different terms. During development, send only demo or DataMate data.
  The README must warn self-hosters and point to local models.
- **Successful injection can still produce a wrong answer.** Verification reduces this but cannot
  eliminate it.
