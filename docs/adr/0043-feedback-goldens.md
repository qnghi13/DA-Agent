# 0043: User feedback, golden cases from thumbs-ups, and feedback rates per agent version

**Status:** Accepted, 2026-11-20. Design: [feedback and goldens](../design/2026-11-20-feedback-goldens.md).

**Context:** The only eval set was DataMate, and the only quality signals in production were the
ones code computes (verify failures, tool errors, repairs). Nothing said whether a user thought an
answer was right, and nothing turned real questions into regression checks.

**Decision:**
- **Feedback:** a member rates a finished run 👍 or 👎 (`PUT /v1/runs/{id}/feedback`, undo with
  `DELETE`). Two levels, not a scale (user, 2026-11-20): a one-second choice gets more answers, means
  the same to every user, and maps to right or wrong. A 👎 can carry reasons from a fixed list
  (`wrong_number`, `misunderstood`, `should_abstain`, `unclear`, `other`), which say where to fix,
  and an optional note of up to 1000 characters. Reasons and note are kept only with 👎. One rating
  per run (`run_feedback`, migration 0016); the audit log records the rating, never the note.
- **Golden cases:** a 👍 on a run with an answer (not unanswerable) makes a golden case with its
  question, dataset, as-of date, and the answer as the expected one. Changing the rating or taking
  it back retires the case; it is kept, not deleted.
- **Reruns:** `python -m da_agent.goldens run --workspace W --yes` answers each active case again
  through `RunHandler.rerun`, in this process, with the source run's job: the same dataset,
  documents, memory snapshot, and earlier conversation turns, and the worker's routes and limits.
  No job is created, so reruns stay out of the user's history. Costs go under
  `golden-<source run>-<suffix>`, which the ledger charges to the run's workspace, so reruns count
  toward its daily budget; each rerun may spend what the workspace has left, up to the per-run
  limit, without the source run's past spending. Without `--yes` it only counts. Each case is
  `same`, `changed`, `no_answer`, or `error`, printed as it finishes and written line by line to a
  JSONL report under `DATA_DIR/goldens/` (users' questions and answers stay outside the repository;
  a stopped rerun keeps what it paid for); a broken case never stops the others. `goldens list` shows the cases and the runs rated
  down with their reasons and notes, for an operator to review.
- **Comparison** (`da_agent.agent.compare`, core, not the DataMate scorer): same kind; numbers
  within a relative 0.01%; text trimmed and case-folded; lists in any order; tables by column name
  with rows in any order. Inside lists and tables, whole numbers must be equal and only other JSON
  numbers get the tolerance; strings such as ids, codes, and phone numbers compare as text.
- **Online signals:** the ops summary's feedback panel shows 👍 and 👎 counts, the share of finished
  runs rated, 👎 reasons by count, and the 👍 rate per agent version (the latest attempt's,
  ADR 0038). Counts and the fixed reason words only, never notes.

**Rejected:**
- An LLM judge on a sample of live runs (option B): costs per run, needs calibrating against user
  feedback first, and the offline challenge cannot use it. Later, with this feedback as its check.
- A 1-5 scale: fewer answers, read differently by each user, unclear as a golden case.
- A separate "save as test" button: one more choice for non-technical users; a 👍 already says it.
- Rerunning cases as queued run jobs: they would join the dataset's history and conversations.
- Reusing the DataMate scorer: competition-specific, and the core must not depend on `eval/`.

**Consequences:**
- Follow-ups (2026-11-21): an empty answer (no value, blank text, no list items or table rows)
  makes no case; a rerun that does not end `answered` counts as `no_answer` even when its value
  matches; removing a rating needs a finished run and is audited only when there was one, in the
  same transaction; the ops panel counts ratings by when they were given; tables with repeated
  column names compare by position.
- A 👍 says the user believes the answer, and they can be wrong. `changed` means look again, not
  failed; a case whose expected answer was wrong is retired by rating the run down.
- A rerun reads the dataset as it is now: a rebuilt dataset can change answers, so a `changed` may
  come from the data, not the code.
- Reruns cost model calls; reruns of one workspace's cases are bounded by `--limit`.
- Feedback rates are as good as the share of runs rated; the panel shows that share beside them.
