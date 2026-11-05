# Threat model

**Status: mostly design.** Controls are planned unless noted. A control counts as done only when a
test proves it. Done so far: the SQL guard and hidden columns on SQLite (`tests/security/`), and keeping
answers away from the model during eval runs (`tests/unit/test_datamate_eval.py`).

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
| LLM-generated code attacks the host or network | Container with `--network none`, read-only, non-root, `--cap-drop ALL`, RAM, CPU, PID, and time limits, no secrets | Tests attempt network, file writes, env reads, fork bomb, memory exhaustion |
| Runner compromised through its Docker socket | Only the Runner holds the socket. It accepts typed requests and builds container options itself, so callers cannot set mounts or flags | Test that request fields cannot change mounts or flags |
| SQL reads files, writes, or attaches databases | sqlglot allows only `SELECT`. DuckDB opens read-only with `enable_external_access` off and a timeout | Tests attempt `COPY`, `read_csv`, `ATTACH` |
| Prompt injection from cells or descriptions | File content is always framed as data. Tools have least privilege | Injection suite through cells and descriptions |
| Malicious spreadsheet: zip bomb, external entities, huge sheets | Size, type, and row limits before parsing. Macros are ignored | Malformed file tests. See the open item below |
| Path traversal through upload names | Generated storage names. Original names are kept only as metadata | Unit tests |
| Cross-workspace access | Every query carries `workspace_id`. One DuckDB file per dataset. The sandbox mounts one dataset | Isolation test across two workspaces |
| Stolen or guessed API key | Keys stored hashed, rate limits, audit log | Unit and API tests |
| Cost runaway | Token and money limits per run, day, and workspace. A breach stops the run cleanly | Budget stop test |
| Personal data in prompts, logs, traces | Sensitive columns masked. Columns marked hidden do not appear in views | Test that a canary value never reaches logs or spans |
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
- **Parser exploits in the Worker (open).** The Worker parses untrusted files and also holds provider
  keys, so a parser bug could expose them. Option: move parsing into a sandbox through the Runner. It
  needs a decision by Day 3, before ingestion is built.
- **Data sent to LLM providers.** Column profiles, sample values, and query results leave the machine.
  Free tiers may use them under different terms. During development, send only demo or DataMate data.
  The README must warn self-hosters and point to local models.
- **Successful injection can still produce a wrong answer.** Verification reduces this but cannot
  eliminate it.
