# User feedback, golden cases, and online quality signals

**Status:** approved 2026-11-20. The user chose option A: no LLM judge. **ADR:**
[0043](../adr/0043-feedback-goldens.md).

## Why

This is step 5 of the harness track. The only eval set today is DataMate, and the only quality
signals in production are ones code computes: verify failures, tool errors, and repairs. Nothing
tells whether the user thought an answer was right.

Production systems close this loop the same way:
- they attach user feedback to runs;
- they turn approved runs into a dataset of regression cases;
- they watch feedback rates per release.

LangSmith and Langfuse do this with feedback scores and datasets.

## Goals and non-goals

### Goals

**Feedback.**
- A member rates an answer 👍 or 👎.
- A 👎 can carry reasons from a fixed list (any number, none required): `wrong_number` (the
  figure is wrong), `misunderstood` (it answered another question), `should_abstain` (it should
  have said the data cannot answer), `unclear` (hard to read or in the wrong form), and `other`.
  It can also carry an optional note (at most 1000 characters).
- Two levels, not a scale (user, 2026-11-20). A one-second choice gets more answers than a 1–5
  scale, means the same to every user, and maps to "right or wrong". The reasons say where to fix.
- There is one rating per run. The latest rating replaces the earlier one, and it can be removed.
- The run view shows the rating. Every change goes to the audit log.

**Golden cases.**
- A 👍 on an answered run, one that has a non-empty answer, makes that run a golden case. The case
  keeps the question, dataset, as-of date, and the answer as the expected one.
- Removing the 👍, or changing it to 👎, retires the case.

**Regression runs.**
- An operator reruns a workspace's active golden cases with today's code:
  `python -m da_agent.goldens run --workspace W [--dataset D] [--limit N]`.
- Each case runs in-process, the way the worker would, with the same dataset, documents, routes,
  and limits. Its costs go to the ledger under `golden-<case>-<uuid>`.
- The CLI writes a JSON report and prints a summary for each case: same, changed, no_answer, or
  error. Before it runs, it says how many cases there are, and it needs `--yes` to spend.
- `goldens list` shows the cases, and the runs rated 👎 with their notes, for review.

**Online quality signals.**
- The ops summary gets a feedback panel: 👍 and 👎 counts for the window, the share of runs rated,
  👎 reasons by count, and the 👍 rate per agent version (ADR 0038).
- It shows counts and the fixed reason words only, never notes, because the ops panels hold no
  user text.
- A 👍 says the user believes the answer, and they may be wrong. A rerun that comes out `changed`
  means "look again", not "failed".

### Non-goals

- An LLM judge (option B, later).
- Comparing golden runs across versions automatically.
- Showing golden cases in the UI.
- Editing an expected answer by hand.
- Feedback on runs that are not finished.

### Anti-goals

- Golden reruns showing up in a user's conversation history.
- Notes in ops panels, logs, or traces.
- One workspace seeing another's feedback or cases.
- A rerun spending money without a clear count and confirmation first.

## Approaches

| Question | Chosen | Rejected, and why |
|---|---|---|
| Online eval | User feedback and code signals, per agent version (user chose A) | LLM judge on a sample (B): costs per run, needs calibrating against feedback first, offline challenge cannot use it |
| What becomes a regression case | A 👍 on an answered run, automatically | A separate "save as test" button: one more choice for non-technical users; a 👍 already says "this is right" |
| Where reruns run | In-process CLI, like the time travel fork, through `RunHandler` | Run jobs on the queue: they would join the dataset's run history and conversations |
| How answers are compared | Core code: same kind, numbers within 0.01% relative, text and list items trimmed and case-folded, list order ignored, tables by column names and a multiset of rows | The DataMate scorer: competition-specific, and the core must not depend on `eval/` |

## Design notes

- **Tables.** Migration 0016 adds two tables:
  - `run_feedback (job_id PK FK, workspace_id, rating up|down, reasons text[] checked against
    the fixed list, note, author, created_at, updated_at)`; reasons and the note are kept only
    with 👎;
  - `golden_cases (id, workspace_id, dataset_id, source_run_id UNIQUE, question, as_of,
    expected jsonb, active, created_at, retired_at)`.
- **API:**
  - `PUT /v1/runs/{id}/feedback` takes `{rating, reasons?, note?}`.
  - `DELETE /v1/runs/{id}/feedback` removes the rating.
  - Both answer 409 unless the run succeeded.
  - The run view gains `feedback: {rating, reasons, note} | null`.
- **UI.**
  - The Answer card gets 👍 and 👎 icon buttons with `aria-pressed`.
  - 👎 opens reason chips and a small note field (all optional, Send or Skip).
  - A second click on the same thumb removes the rating.
- **Comparison.** Lives in `da_agent.agent.compare`: `compare_answers(expected, actual) ->
  "same" | "changed" | "no_answer"`.
- **Ops.** The version of a run is the agent version of its latest attempt, from `job_attempts`.

## Left to the builder

- Icon placement and wording.
- The CLI table layout.
- The report file name, under `--output`, by default `goldens-<timestamp>.json` in the current
  folder.
