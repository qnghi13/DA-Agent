# 0045: Adding files to a dataset creates a new version inside the same dataset

**Status:** Accepted, 2026-11-22. Design: [Add files to a dataset](../design/2026-11-21-add-files.md).

**Context:** A user chatting about a dataset often finds data missing: next month's orders, or a
customer list. The only path was a new upload, which loses the conversation and the notes and
metrics in `dataset_memories` (keyed by `dataset_id`), and spends the spec proposal again. Users are
non-technical first, so the system has to work out what the new file is. The worst outcome is a
number counted twice because a file overlapped old data and nobody was asked.

**Decision:**
- **Versions inside one dataset.** `dataset_versions` (migration 0017) holds one row per version
  with its files, spec, summary, status, and choices. `datasets.current_version` points at the one
  runs read. The dataset stays `ready` on its current version while a new one is checked; at most
  one version is open (partial unique index). Every ready dataset has a version 1 (backfilled).
  Folders are siblings named `<dataset id>-v<n>`, so the Runner is unchanged.
- **Endpoints:** `POST /v1/datasets/{id}/files` (same limits as `POST /datasets`, counted over all
  files of the new version, plus a limit on versions), `GET .../versions`, `POST
  .../versions/{n}/approve`, `POST .../versions/{n}/discard`. The check is a `version` job in the
  worker, with the steps `check` and `build`.
- **Code decides, the model only proposes.** Each raw table of the new version is matched by header:
  unchanged, grown (same header), or new. A near match (a column added or renamed) is a new table
  and the review names the table it resembles. Grown tables are built with the existing spec and
  checked: overlap (by key, exact row, or only by date range) and the quality of the new rows
  (parse rates, rows moved to `_excluded`, counted over new rows only).
- **Three outcomes.** Same shape, no overlap, quality ok: applied with no model call and a short
  summary. Overlap: the version waits (`needs_choice`) for a choice per table: replace the old
  file, drop the overlapping new rows, or keep both. A new table or failing quality: the model
  proposes (`proposed`): spec entries for the new tables only, and before/after repairs limited to
  the failing columns or rules of an existing table. Code merges these and keeps every existing
  table spec, including the user's edits, exactly.
- **Rules that came out of review:**
  - Only code writes `drop_overlap`, from the user's choice; the model's output has it stripped.
  - `drop_overlap` is offered only for a table with a key or exact-row overlap. A table that
    overlaps only by date range offers replace or keep both. `changes` lists `allowed_choices`
    per table, and the approve endpoint answers 422 for anything else.
  - Approve validates before queueing (422): choices only for tables reported with overlap,
    `replace_files` naming current files, spec edits only for new tables, accepted repairs a subset
    of the proposed ones and the repaired spec valid. Proposed repairs start ticked in the UI; Apply
    is the approval.
  - A new raw table the model did not cover gets a plain `TableSpec`, so nothing lacks a spec.
  - The worker re-reads the version under lock and does nothing when it is discarded or in an
    unexpected state. `apply` checks the status it expects and runs in one transaction, so a retried
    or late attempt never applies twice or revives a discarded version. A crash marks the version
    failed; the current version keeps serving.
  - Discard removes the version's upload folder only. Its build folder exists only right before
    `apply`, and a refused apply removes it.
- **Runs and old answers.** Every run records the dataset version it read, and exports, queries,
  and memory reads use that version. Turns from an older version stay as they were: the prompt
  labels a turn as older when its version is lower than **the run's own** version, not the
  dataset's current one, so a resumed run builds the same prompt ([ADR 0030](0030-conversation-and-dataset-memory.md)).
  The UI shows a divider and a badge. `dataset_changed_since_run` is true for a run's version when
  an unapplied version exists, and keeps the earlier not-ready or updated-after rule when the run's
  version is the current one. A run on an older version is never flagged by a newer upload.
- **UI.** An "Add files" dialog in the chat, a status line and review screen (`VersionReview`)
  for choices, new tables, and repairs, and a version list in "About this data".

**Rejected:**
- **B, a new dataset linked to the old one:** the conversation, notes, and metrics are tied to the
  dataset id, so each would need a copy or a merge, and "which dataset is current" moves to the user.
- **C, append rows into the existing tables in place:** the data changes under a running run, a
  half-installed state can be read, and an earlier answer changes when re-run. No version to point
  a run at, so no labelled history.
- Recomputing old answers on new data, rollback, and deleting a file without replacing it are
  non-goals for now.

**Consequences:**
- Every version ingests all of its files again; there is no incremental ingest.
- Open items: a Runner outage during a version check currently marks the version failed and deletes
  the upload (it should retry, as exports and queries do; to fix before merge). A dead database
  connection can leave a version in `checking`, which discard refuses. A per-run budget stop shows
  as `proposal_failed`, not as the budget message. A replace in the build step can remove a table
  without the stale-metric confirmation. The version limit and the open-version check run after
  the files are streamed. The dialog does not stop two files replacing the same old file.
- `worker/versions.py` and `api/app.py` grew past the usual size.
- The run list does not carry `dataset_version`, so dividers appear after each run loads.
- Not yet run with a real model, nor in a browser. The end-to-end check runs after the final
  review, with the user's approval of the cost.
