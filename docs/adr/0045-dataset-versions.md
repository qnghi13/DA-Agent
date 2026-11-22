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
  Folders are siblings, so the Runner is unchanged: the build is `<dataset id>-v<n>`, the upload
  `<dataset id>-v<n>-<8 hex>`. The random suffix means a folder left by a crashed upload, which
  has no version row, never blocks the next upload; the row stores the name, so nothing parses it.
- **Endpoints:** `POST /v1/datasets/{id}/files` (same limits as `POST /datasets`, counted over all
  files of the new version, plus a limit on versions that counts only applied and open versions,
  since failed and discarded ones keep no build), `GET .../versions`, `POST
  .../versions/{n}/approve`, `POST .../versions/{n}/discard`. The check is a `version` job in the
  worker, with the steps `check` and `build`.
- **Code decides, the model only proposes.** Each raw table of the new version is matched by header:
  unchanged, grown (same header), or new. A near match (a column added or renamed) is a new table
  and the review names the table it resembles. Grown tables are built with the existing spec and
  checked: overlap (by key, exact row, or only by date range) and the quality of the new rows
  (parse rates, rows moved to `_excluded`, counted over new rows only). The upload marks each new
  file with `added_in`; a version with no file so marked fails rather than count every row as old.
  New files are checked against older files and against the new files before them in upload order
  (the version's `files` order, carried to the sandbox in `BuildRun.new_sources`), so two copies in
  one upload are found. An overlap names the earlier new files; a date check uses each earlier new
  file's own dates, so November sent after December is no overlap.
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
    `replace_files` naming one of that table's own current files (each `changes` entry lists its
    `files`; the build step checks the same rule and the UI offers only those), spec edits only for
    new tables, accepted repairs a subset of the proposed ones and the repaired spec valid. Proposed
    repairs start ticked in the UI; Apply is the approval.
  - `drop_overlap` names, for each new file, the older files and the new files before it, and
    drops rows already in them into `_excluded` (`already in <files>`).
  - A new raw table the model did not cover gets a plain `TableSpec`, so nothing lacks a spec.
  - The worker re-reads the version under lock and does nothing when it is discarded or in an
    unexpected state. `apply` checks the status it expects and runs in one transaction, so a retried
    or late attempt never applies twice or revives a discarded version. A crash marks the version
    failed; the current version keeps serving.
  - An outage is retried, not failed: the Runner unavailable, a sandbox that could not start, or
    every model route failing raises `RetryableJobError` and keeps the upload, as exports and
    queries do. An outage during a proposal's validation is never shown to the model. On the last
    attempt the version fails with a readable reason, so it does not stay open.
  - `open_version` refuses, under the dataset's row lock, when the current version is no longer
    the one whose files the upload linked, or the version number is no longer the one written into
    `added_in` (409, "the data changed while you uploaded; try again"). An open version and the
    limit are also refused before any file is saved.
  - Discard removes the version's upload folder only. Its build folder exists only right before
    `apply`, and a refused apply removes it. A version left in `checking` or `building` by a job
    that ended (succeeded, failed, or cancelled) can be discarded too: nothing would move it on.
    The open version carries `stuck`, and the chat's status card offers Discard for it.
- **Runs and old answers.** Every run records the dataset version it read, and exports, queries,
  and memory reads use that version. Turns from an older version stay as they were: the prompt
  labels a turn as older when its version is lower than **the run's own** version, not the
  dataset's current one, so a resumed run builds the same prompt ([ADR 0030](0030-conversation-and-dataset-memory.md)).
  The UI shows a divider and a badge. `dataset_changed_since_run` is true when the run's own
  version is not applied or its build folder is gone; when that version is the current one, it
  also keeps the earlier rule: a dataset that is not ready, or was rebuilt after the job was
  queued, counts as changed. A newer upload never flags a run on an older version.
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
- Open items: a per-run budget stop shows as `proposal_failed`, not as the budget message. A
  `drop_overlap` on a table with more than 20 older files fails late (the spec allows 20 `against`
  files). Apply with every proposed repair unticked fails the version. The dialog does not stop two
  files replacing the same old file. A build folder installed just before a crash stays on disk
  when the version is then discarded (the API has no datasets root).
- `worker/versions.py` and `api/app.py` grew past the usual size.
- The run list does not carry `dataset_version`, so dividers appear after each run loads.
- Not yet run with a real model, nor in a browser. The end-to-end check runs after the final
  review, with the user's approval of the cost.
