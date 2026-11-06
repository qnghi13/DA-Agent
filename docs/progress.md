# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.

**Last updated:** 2026-11-06
**Phase:** Days 3–6, Data and tools ([plan §6](plan.md#6-roadmap)). `exclude_rows` checks done, uncommitted.

## Next step

1. Agent on the clean DuckDB files in `eval/reports/datasets-dev/`, `run_sql` through the Runner.
   Then the `bootstrap` baseline (ask first), with `datasets-dev-v1` (no exclusion) as the
   comparison, and the Foundation learning note.
2. Known gap, left until real data needs it: a heading row holding a group total and the group's
   date (`syn_total_above`). Excluding it leaves its rows without a date.

## Done

- Dev environment, design docs, ADRs 0001–0010, README, LICENSE, project structure.
- Scorer (`eval/scorer/`, rulebook §7, §9): 3000/3000 on the real answers. Splits in
  `eval/splits/splits.json`: 24 dev, 6 holdout shops; sets `bootstrap`, `smoke`, `regression`.
- Minimal agent: `LLMClient`, SQL guard, bounded tool loop, tracing, resumable `eval/datamate`
  runner. Model: Groq `gpt-oss-20b`; `search_rules` (ADR 0008).
- Runner (`src/da_agent/runner/`) and sandbox image (`sandbox/`): all escape tests pass on real
  containers.
- Raw ingestion runs in the sandbox (ADR 0009): csv, xlsx, SQLite to `raw.duckdb`, header detection,
  masked profiles. All 24 dev shops load (1–46 s).
- `DatasetSpec` pipeline: LLM proposes, sandbox builds `dataset.duckdb` with `_quarantine`, parse-rate
  feedback. 24/24 dev shops, parse rates 99.9–100%, in `eval/reports/datasets-dev/` (gitignored).
- `exclude_rows` (ADR 0010): rules, `_excluded`, rare shapes with sums, and checks for kept
  subtotals and for rules that remove records (full rows, identifiers, share). Rules still failing
  after the last attempt are dropped. Measured with `eval/ingestion/` (labels, synthetic, scorer).
  `datasets-dev/` (prepare-v4, biz_109 and biz_124 from v4b): 24/24 ok, parse 99.98%+, exclusion
  precision and recall 100% on 2,032 labelled rows. Old build kept in `datasets-dev-v1/`.

## Decisions needed

- **Days 3–6:** Worker to Runner transport, container per request or per run ([open questions](architecture.md#open-questions)).

## Not verified

- Gemini has made no real call; Ollama on the 4 GB GPU was too slow. `deploy/compose.dev.yml` never ran (no Compose plugin). Phoenix runs as container `da-agent-phoenix`.

## Waiting on the user

- **Days 7–11:** agree to add $5–10 for Haiku 5.5. **Before Day 20:** install the Compose plugin.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $0.50 (agent runs $0.06, spec proposals $0.44).

## Watch out

- A question uses 48–75k tokens, about $0.005; a runaway one 130k.
- `gpt-oss-20b` specs vary run to run (one shop went 100% to 0%). Compare totals, never one shop.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
- Answers have no row labels: ingestion needs our own labels, the score checks end to end. Keep the
  declarative spec (user, ADR 0006); a sandboxed code step is an open question.
- Scorer interpretations: a wrong `type` is wrong; table columns match by name only if all names match.
