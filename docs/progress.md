# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.

**Last updated:** 2026-11-06
**Phase:** Days 3–6, Data and tools ([plan §6](plan.md#6-roadmap)). Runner and sandbox done,
uncommitted. File parsing runs in the sandbox ([ADR 0009](adr/0009-parse-uploads-in-sandbox.md)).

## Next step

1. Ingestion as a plain library, run in the sandbox through a new Runner job kind: safety checks,
   raw read (csv, xlsx, SQLite), table detection, profile. Then `DatasetSpec`, DuckDB loader, and
   `run_sql` through the Runner. Exit: 30/30 shops load with parse and quarantine reports.
2. Then `bootstrap` baseline with gpt-oss-20b (ask first, about $0.18) and the Foundation learning note.

## Done

- Dev environment, design docs, ADRs 0001–0009, README, LICENSE, project structure.
- Scorer (`eval/scorer/`, rulebook §7 and §9), checked on the real answers: 3000/3000 when correct.
- Splits (`eval/splits/splits.json`): 24 dev and 6 holdout shops; sets `bootstrap`, `smoke`, `regression`.
- Minimal agent (`src/da_agent`): `LLMClient`, Groq and Gemini adapters, SQL guard, read-only SQLite
  dataset, bounded tool loop, tracing. `eval/datamate` runs it and stays resumable.
- Checked end to end with a scripted model on biz_123, with spans arriving in Phoenix.
- Model: Groq `openai/gpt-oss-20b`, paid. `search_rules` halves the prompt (ADR 0008).
- Runner (`src/da_agent/runner/`) and sandbox image (`sandbox/`): 11 escape tests pass on real
  containers (network, writes, env, other datasets, socket, caps, fork bomb, memory, timeout, output).

## Decisions needed

- **Days 3–6:** Worker to Runner transport, and container per request or per run. See
  [architecture open questions](architecture.md#open-questions).

## Not verified

- Gemini has made no real call. Local Ollama (qwen3.5:2b, qwen3:4b on a 4 GB GPU) was too slow
  and never answered; container `da-agent-ollama` is stopped.
- `deploy/compose.dev.yml` has not run, because this machine lacks the Compose plugin. Phoenix runs
  through `docker run` as container `da-agent-phoenix`.
- Subagents are registered but unused. `permissionMode: plan` on `system-architect` is untested.
- Most security controls in the threat model are still planned.

## Waiting on the user

- **Days 7–11:** agree to add $5–10 for Haiku 5.5. Claude gives notice first.
- **Before Day 20:** install the Compose plugin, or enable Docker Desktop's WSL integration.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $0.06 (Groq gpt-oss-20b, 3 runs of 3 questions plus a cache probe).

## Watch out

- A question uses 48–75k tokens (6–9 calls of 6–9.5k input), about $0.005; a runaway one 130k.
- SQL runs in the eval process until the sandbox exists. Do not use this path on untrusted files.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
- Scorer interpretations: a wrong `type` is wrong; table columns match by name only if all names match.
