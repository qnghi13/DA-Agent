# 0019: Dataset lifecycle: ingest proposes, the user approves, and runs open only ready datasets

**Status:** Accepted, 2026-11-08

**Context:** The ingestion pipeline (ADRs 0006, 0009, 0010) turns an upload into a clean DuckDB
dataset, and evals accept its spec without review. The product flow puts a user between proposal
and use: the user approves the proposed spec or edits it. Run jobs need datasets, and must never
open another workspace's.

**Decision:**
- A `datasets` row moves `uploaded` → `proposed` (ingest job) → `ready` (approved), or through
  `building` when the user edits the spec, or to `failed`. Only `ready` datasets can be queried.
- The ingest job runs `prepare_dataset` and installs the chosen build under
  `datasets_root/<dataset id>` straight away, with the spec, a review summary (tables, parse
  problems, rules the pipeline dropped, deduplicated tables), and the uploaded documents. Approving
  the spec unchanged needs no new build, so it is instant.
- An edited spec is validated, stored, and its build job queued in one transaction, so a dataset is
  never `building` without a job to build it. The build job uses `rebuild_dataset`, with no model
  call.
- A failed ingest or build is a result for the user, not a job failure: the dataset is marked
  `failed` with a readable reason, and the job completes.
- `DatasetOpener` gives a run job a dataset only if it is `ready` and belongs to the job's
  workspace. Its documents are the uploaded documents plus the uploader's description.
  `PermanentJobError` fails the job with a safe message, such as "dataset not found".
- The dataset id is the upload id, the build directory name, and the id the Runner mounts, so the
  opener's `datasets_root` must be the Runner's.

**Consequences:**
- A retried ingest job runs the pipeline again, model calls included. The job's run id keys its
  cost records, so the cost is still counted.
- Installing a build renames the old directory away first; no run reads it meanwhile, because a
  dataset being built is not `ready`.
- Tests use a fake Runner, so the lifecycle is tested without Docker; the Runner itself has its own
  sandbox tests.

**Rejected:**
- Building only after approval: approval would wait minutes for a build that the proposal step
  had already made to check parse rates.
- Letting run jobs open datasets by path: the workspace check would depend on every caller.
