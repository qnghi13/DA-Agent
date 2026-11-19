# 0038: Every job attempt records the agent version that ran it

**Status:** Accepted, 2026-11-19. Design: [run manifest](../design/2026-11-19-run-manifest.md).

**Context:** When a score drops or a run looks wrong, nothing said which code, prompt, model
routes, or limits produced it. `llm_calls` kept the provider and model of each call, not the git
version, the text the model saw, or the limits. A job can be tried several times, by different
workers and across a deploy, so one run can mix versions: a new worker may continue a checkpoint
that old code wrote.

**Decision:**
- A **manifest** (`da_agent.manifest`) holds the git sha and whether the checkout had changes, one
  fingerprint per prompt family (agent, ingestion), each route's provider, model, and request
  options, and the configured limits. Its **version id** is the first 12 hex characters of
  SHA-256 over its canonical JSON, so the same configuration always gets the same id.
- A prompt fingerprint hashes **what the model sees**: the upper-case constants of the prompt
  modules that are plain data (prompt text, tool specs, tool names, limits the model reads) and
  the text that functions write, rendered with no data. Comments and refactors do not change it;
  the git sha covers every other code change.
- The worker registers its manifest in `agent_versions` when it starts and writes one
  `job_attempts` row per claim (worker, version, trace id), for every job kind. Spans carry
  `da_agent.agent_version` and `gen_ai.agent.version` (OpenTelemetry GenAI conventions).
- `run_graph` puts the version into the LangGraph config metadata, which the checkpointer copies
  into each checkpoint's metadata. Before a run job starts, the worker reads the latest
  checkpoint's version; a different one sets `resumed_from_version` on the attempt and the span.
  A checkpoint without a version (from before this change) counts as unknown, not different.
- Eval runs write the manifest into `metrics.json`, including the run options that change what
  the model sees (agent kind, reports, metric definitions).
- Images get the sha from the build arg `DA_AGENT_GIT_SHA` (release: `$GITHUB_SHA`; Compose: from
  the environment, else `unknown`). Locally the worker and eval read it from git.

**Rejected:**
- Hashing the source files: comments and refactors would change the version.
- A version constant bumped by hand: it gets forgotten.
- A jsonb array of versions on `jobs` beside `trace_ids`: the arrays drift apart when tracing is
  off, because no trace id is appended.
- Comparing a resume with the previous attempt row instead of the checkpoint: wrong when that
  attempt crashed before it wrote a checkpoint.

**Consequences:**
- Results can be grouped by version later (dashboard, eval comparison) with one `GROUP BY`; that
  is not built.
- What to do when a run is resumed by another version (continue, restart, refuse) stays open. The
  mark counts how often it happens before a policy is built.
- An image does not know whether its source had changes, so `dirty` is null there. Eval runs on
  uncommitted code record `dirty: true`, and the prompt fingerprint tells such runs apart.
- Text written inside functions other than the sampled ones changes only the git sha, not the
  prompt fingerprint.
