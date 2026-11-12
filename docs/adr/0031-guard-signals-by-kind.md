# 0031. Guard signals are counted by kind

**Status:** Accepted (2026-11-13). Extends [0026](0026-operator-dashboard.md) and
[0007](0007-code-first-verification.md).

**Context:** The dashboard counted failed tool calls and failed verify passes, but not why they
failed. A SQL guard refusal, a timeout, and a typo in a column name were one number, and so were
an untraced number and a period with no data. A run that verify sent back and that then answered
was invisible: its outcome is just "answered". Failure analysis of eval runs needs these splits,
and an operator needs them to tell probing from model mistakes.

**Decision:**
- Every failed tool step carries `error_kind`, a fixed word written by code: `rejected` (SQL
  guard or a hidden column), `timeout`, `limit` (sandbox killed), `engine`, `arguments`, `crash`,
  `chart`, `no_match`, `unknown_tool`, `answer_format`. `QueryError` carries the first four.
- Verify returns `Problem(kind, message)`: `evidence`, `tracing`, `inputs`, `period`, `fanout`,
  `unreadable`. `check_answer` still returns the messages. The graph keeps `verify_log`, an
  append-only list of the kinds each pass found, or `passed`.
- The kinds go to the tool span (`da_agent.tool.error_kind`), the verify span
  (`da_agent.verify.kinds`), progress events (`error_kind`, `checks`), `AgentResult`, eval rows
  (`error_kinds`, `verify_log`), and eval metrics (`tool_error_kinds`, `verify_kinds`,
  `runs_repaired`, `runs_saved_by_repair`).
- The quality panel counts both by kind from `run_events`, and `runs_saved`: runs with a failed
  verify pass whose outcome is still "answered". Events from before this change count as
  `unknown`.

**Consequences:**
- Kinds are code words, never user text, so ADR 0026's rule holds: the dashboard shows no
  questions, answers, problems, or SQL errors.
- `verify_log` is a new optional state key with an `add` reducer; old checkpoints and job rows
  load with an empty log.
- A new check must add its kind here and in the UI; an unlisted kind still shows, by its name.

**Rejected:**
- Classifying the stored problem text with patterns: breaks when wording changes, and reads user
  data in the operator's query.
- A separate guard-events table: the events and results already hold every pass and step.
