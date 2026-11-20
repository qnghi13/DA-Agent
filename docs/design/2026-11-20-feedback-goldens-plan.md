# Feedback, golden cases, and online quality signals: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three things:
- 👍 and 👎 feedback on answers;
- golden cases made from 👍 answers, which an operator can rerun as a regression check;
- feedback rates per agent version in the ops summary.

**Architecture:**
- Two new tables, with a storage module for each.
- Feedback endpoints on runs.
- A core answer comparator.
- An operator CLI that reruns golden cases through `RunHandler` in-process.
- A feedback panel in ops.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, Postgres, LangGraph, React and TypeScript with
vitest.

**Spec:** [2026-11-20-feedback-goldens.md](2026-11-20-feedback-goldens.md)

## Global Constraints

- **Tests first.** Tests use a fake LLM. Postgres tests are integration tests.
- **Notes stay private.** Notes never appear in ops output, logs, or spans.
- **Workspaces stay separate.** Every query is scoped to the workspace.
- **Number tolerance.** A relative tolerance of 1e-4 when comparing numbers.
- **Reruns ask first.** A rerun needs `--yes` and prints the case count first.
- **No new dependencies.**

## Review Focus

1. Rating a run twice, switching 👍 to 👎, and removing the rating: there is exactly one feedback
   row, and the golden case is active only while the rating is 👍.
2. A 👍 on a run with no answer, or on an unanswerable answer, records the feedback and creates no
   golden case.
3. A golden case whose dataset was deleted or rebuilt: the rerun reports an error, or a changed
   answer, and carries on with the other cases.
4. Tables with columns in a different order, and lists in a different order: these compare as
   the same.
5. Feedback by a member of another workspace returns 404. A note longer than 1000 characters
   returns 422.

---

### Task 1: Tables and storage

**Files:**
- Create:
  - `src/da_agent/storage/migrations/0016_feedback.sql`
  - `src/da_agent/storage/feedback.py`
- Test: `tests/integration/test_feedback_store.py`

**Interfaces.** It produces:
- `Feedback(rating, reasons, note, author, updated_at)`, and `REASONS = ("wrong_number",
  "misunderstood", "should_abstain", "unclear", "other")`
- `set_feedback(conn, job, rating, reasons, note, author) -> None`. It also adds or retires the golden
  case, in one transaction.
- `clear_feedback(conn, job_id, workspace_id) -> None`
- `feedback_of(conn, job_id, workspace_id) -> Feedback | None`
- `GoldenCase(id, dataset_id, source_run_id, question, as_of, expected, active)`
- `golden_cases(conn, workspace_id, dataset_id=None, active=True) -> list[GoldenCase]`
- `disliked_runs(conn, workspace_id, limit) -> list[dict]`

**Steps:**
- [ ] **Tests.** Cover the cases in Review Focus 1 and 2, an empty or whitespace note, reasons
  kept only with 👎, and an unknown reason refused.
- [ ] Run them and see them fail.
- [ ] Implement.
- [ ] Run them and see them pass.

### Task 2: Feedback API

**Files:**
- Modify `src/da_agent/api/app.py`.
- Test `tests/integration/test_api.py`.

**Steps:**
- [ ] **Tests:**
  - `PUT` returns 200 with the feedback.
  - `GET` of the run shows the feedback.
  - `DELETE` returns 204.
  - A run that has not succeeded returns 409.
  - Another workspace's run returns 404.
  - A note longer than 1000 characters returns 422.
  - Each change writes the audit action `run.feedback`, and its detail holds only the rating,
    never the note.
- [ ] Implement, then run the tests and see them pass.

### Task 3: Ops feedback panel

**Files:**
- Modify `src/da_agent/storage/ops.py`.
- Test `tests/integration/test_ops.py`.

**Steps:**
- [ ] **Test.** `summary()["feedback"]` gives `{up, down, rated_share, reasons: {reason: n}, by_version: [{version,
  up, down, up_rate}]}` for the window. Runs with no attempt row count under version `unknown`.
  No note text appears anywhere in the summary.
- [ ] Implement, then run the test and see it pass.

### Task 4: UI thumbs

**Files:**
- Modify:
  - `web/src/api.ts` (`setFeedback`, `clearFeedback`, `Run.feedback`)
  - `web/src/components/chat/run-message.tsx`
  - `web/src/ops.ts` and `OpsDashboard.tsx` (the panel)
- Test `web/src/feedback.test.ts`.

**Steps:**
- [ ] **Tests:**
  - The API calls use the right paths and methods.
  - A pure helper `nextFeedback(current, clicked)` returns the rating to set, or `null` to
    remove it.
- [ ] Implement:
  - the buttons, the 👎 reason chips, and the note field;
  - the ops panel, with counts and the 👍 rate per version.
- [ ] Run `npm test` and `tsc`.
- [ ] Click through it in the running app (no model call is needed: rate an existing run).

### Task 5: Answer comparator

**Files:**
- Create `src/da_agent/agent/compare.py`.
- Test `tests/unit/test_compare.py`.

**Steps:**
- [ ] **Tests:**
  - numbers within the tolerance, and numbers outside it;
  - text compared with case-folding and trimming;
  - lists in a different order;
  - tables with columns in a different order;
  - a different kind of answer;
  - `None` as the actual answer gives `no_answer`;
  - unanswerable compared with unanswerable gives `same`.
- [ ] Implement, then run the tests and see them pass.

### Task 6: Golden rerun CLI

**Files:**
- Create `src/da_agent/goldens.py`.
- Test `tests/integration/test_goldens.py`.

**Steps:**
- [ ] **Tests:**
  - `list` prints the cases and the 👎 runs.
  - `run` without `--yes` prints the count and spends nothing (the fake adapter is not called).
  - `run --yes` reruns each case through `RunHandler`. With a scripted adapter, one case gives
    `same` and one gives `changed`, and both are written to the report.
  - A case whose dataset is missing is reported as `error`, and the run continues.
  - The rerun's run ids start with `golden-`, and no job is created.
- [ ] Implement, then run the tests and see them pass.

### Task 7: Docs

- [ ] ADR 0043.
- [ ] Learning note 22 (Vietnamese).
- [ ] `architecture.md`, README (CLI usage), and `progress.md`.

### Final

- Run `uv run --env-file .env pytest`, `ruff`, `npm test`, and `tsc`.
- Then a fresh review of the whole branch on the most capable model.
- An optional real check, with your go-ahead: rate one run 👍 in the Compose UI, then rerun the
  golden case once on Groq, for about $0.002.
