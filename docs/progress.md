# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.

**Last updated:** 2026-11-06
**Phase:** Days 3–6, Data and tools ([plan §6](plan.md#6-roadmap)). `DatasetSpec` pipeline done,
uncommitted.

## Next step

1. Subtotal rows ("TỔNG THÁNG 2", blank-product total rows) sit inside sales tables of at least
   biz_104, 107, 110, 130 and would double count sums. Proposed: a declarative `exclude_rows` rule
   in the spec, a profile hint, and an `_excluded` table. Needs the user's OK to rerun proposals.
2. Agent on the clean DuckDB files (`eval/reports/datasets-dev/<shop>/`), `run_sql` through the
   Runner. Then the `bootstrap` baseline (ask first) and the Foundation learning note.

## Done

- Dev environment, design docs, ADRs 0001–0009, README, LICENSE, project structure.
- Scorer (`eval/scorer/`, rulebook §7 and §9), checked on the real answers: 3000/3000 when correct.
- Splits (`eval/splits/splits.json`): 24 dev and 6 holdout shops; sets `bootstrap`, `smoke`, `regression`.
- Minimal agent: `LLMClient`, adapters, SQL guard, SQLite dataset, bounded tool loop, tracing,
  resumable `eval/datamate` runner. Model: Groq `gpt-oss-20b`; `search_rules` (ADR 0008).
- Runner (`src/da_agent/runner/`) and sandbox image (`sandbox/`): escape tests pass on real
  containers (network, writes, env, other datasets, socket, caps, fork bomb, memory, timeout, output).
- Raw ingestion (`src/da_agent/ingestion/`) runs in the sandbox as an `ingest` job: csv, xlsx,
  SQLite to text tables in `raw.duckdb` with `_source`/`_row`, header detection, same-header sheets
  merged, profiles with sensitive columns masked. All 24 dev shops load (1–46 s each).
- `DatasetSpec` (`spec.py`, `propose.py`, `build.py`, `pipeline.py`): LLM proposes, sandbox builds
  `dataset.duckdb` with `_quarantine`, parse-rate feedback. 24/24 dev shops ok, parse rates
  99.9–100%, in `eval/reports/datasets-dev/` (gitignored).

## Decisions needed

- **Days 3–6:** Worker to Runner transport, and container per request or per run. See
  [architecture open questions](architecture.md#open-questions).

## Not verified

- Gemini has made no real call. Local Ollama on a 4 GB GPU was too slow (container stopped).
- `deploy/compose.dev.yml` has not run, because this machine lacks the Compose plugin. Phoenix runs
  through `docker run` as container `da-agent-phoenix`.

## Waiting on the user

- **Days 7–11:** agree to add $5–10 for Haiku 5.5. Claude gives notice first.
- **Before Day 20:** install the Compose plugin, or enable Docker Desktop's WSL integration.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $0.25 (agent runs $0.06, spec proposals $0.19).

## Watch out

- A question uses 48–75k tokens (6–9 calls of 6–9.5k input), about $0.005; a runaway one 130k.
- SQL runs in the eval process until the sandbox exists. Do not use this path on untrusted files.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
- Scorer interpretations: a wrong `type` is wrong; table columns match by name only if all names match.
