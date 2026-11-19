# Run manifest: which agent produced each run

**Status:** built 2026-11-19. **ADR:** [0038](../adr/0038-run-manifest.md).

## Why

When a score drops or a run looks wrong, nothing says which code, prompt, model routes, or limits
produced it. `llm_calls` records the provider and model of each call, but not the git version, the
prompt text the model saw, or the limits. A job can also be tried several times, by different
workers and after a deploy, so one run can mix versions. A manifest per attempt is the base for
later harness work: grouping results by version, record and replay, canary runs.

## Goals and non-goals

- Every job attempt records the agent version that ran it: run, ingest, export, and query jobs.
- Eval runs (`eval/datamate/run.py`) write the same manifest into `metrics.json`.
- Spans carry the version, so a trace says which agent produced it.
- A run resumed from a checkpoint written by another version is marked, on the attempt row and on
  the span.
- Non-goals: comparing versions in the dashboard or eval reports (option B, later); a policy for
  resuming across versions such as restarting the run (option C, decided after the mark shows how
  often it happens); UI changes; changing the prompt or graph.
- Anti-goals: secrets in the manifest (API keys, tokens, database URLs with passwords); a version
  that changes when only comments or formatting change.

## Approaches

| Question | Chosen | Rejected, and why |
|---|---|---|
| Prompt version | Hash of what the model sees: prompts and tool specs rendered with no data (agent system and plan prompts, notices, tool specs for every dialect, the ingestion proposal prompt) | Hash of source files: changes on comments and refactors. A hand-bumped constant: gets forgotten |
| Storage | `agent_versions` (id = hash of the manifest, manifest jsonb, first seen) and `job_attempts` (job, attempt number, worker, trace id, version id, resumed-from version) | A jsonb array on `jobs` beside `trace_ids`: the two arrays drift apart when tracing is off |
| Resumed by another version | The version goes into the LangGraph config metadata, which the checkpointer copies into each checkpoint's metadata (checked in langgraph-checkpoint 4.3.1, `get_checkpoint_metadata`); a resume compares it with the current version | Compare with the previous attempt row: wrong when that attempt crashed before writing a checkpoint |

The user chose all three on 2026-11-19, and scope A with the resume mark.

## Design

- **Manifest** (built once per worker process, and once per eval run):
  - `code`: git sha and a `dirty` flag. From a build arg baked into the image as an environment
    variable; locally from `git rev-parse` and `git status --porcelain`; `unknown` when neither works.
  - `prompts`: one hash per prompt family (`agent`, `ingestion`), from the rendered fixed text.
  - `routes`: provider and model of each route, in order, and non-secret options (vLLM seed and
    extra body). No keys, no base URLs with credentials.
  - `limits`: the configured `AgentLimits` and per-question token budget. Per-workspace money left
    today varies per job and stays out.
  - The version id is a short hash of the canonical JSON of the manifest, so the same configuration
    always gets the same id.
- **Worker:** registers the manifest in `agent_versions` at start (insert if absent), and writes a
  `job_attempts` row when it claims a job, with the trace id when tracing is on. `jobs.trace_ids`
  stays as it is.
- **Run jobs:** the graph's config metadata carries `agent_version`. Before streaming, the worker
  (or `run_graph`, the builder's call) reads the saved checkpoint's metadata; a different version
  sets `resumed_from_version` on the attempt row and `da_agent.resumed_from_version` on the span.
  A checkpoint without a version (written before this change) counts as unknown, not as a mismatch.
- **Spans:** the job span and `agent.run` carry `da_agent.agent_version`; `gen_ai.agent.version`
  is added beside it, following the OpenTelemetry GenAI conventions.
- **Eval:** `metrics.json` gets the full manifest under `manifest`.

## Constraints

- Core code stays free of eval and DataMate code. The manifest builder lives in core, and eval
  calls it.
- A plain SQL migration, as in ADR 0017. Existing jobs have no attempt rows, and every reader must
  accept that.
- The manifest is computed without model calls, so this change costs nothing to verify.

## Left to the builder

Where the resume check lives (worker or `run_graph`), the exact module for the manifest builder,
the hash length, and whether the API returns the version in a run view (only if it is one line).
