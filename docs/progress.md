# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.

**Last updated:** 2026-11-06
**Phase:** Days 3–6, Data and tools ([plan §6](plan.md#6-roadmap)). `exclude_rows` done, uncommitted.

## Next step

1. Measure subtotal handling without overfitting dev: a labelled list of non-record rows in dev
   tables, plus synthetic files not taken from DataMate (English, total above its group, grand
   total, notes mid-table, clean tables). A script reports precision and recall.
2. Then decide on code checks (ask first): "row equals the sum of the rows above" as a profile hint
   and post-build problem, and a problem for rules that exclude normally filled rows.
3. Agent on the clean DuckDB files, `run_sql` through the Runner. Then the `bootstrap` baseline
   (ask first), comparing with and without `exclude_rows`, and the Foundation learning note.

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
- `exclude_rows` (ADR 0010): declarative rules, `_excluded`, rare row shapes in the profile, 10%
  guard. Rerun in `eval/reports/prepare-v2/` ($0.06): by hand count, row recall about 51% and
  precision about 95% (missed biz_104, 107 and biz_130 monthly totals; dropped 60 real rows in
  biz_117 purchases). Not promoted to `datasets-dev/`.

## Decisions needed

- **Days 3–6:** Worker to Runner transport, container per request or per run ([open questions](architecture.md#open-questions)).

## Not verified

- Gemini has made no real call. Ollama on the 4 GB GPU was too slow.
- `deploy/compose.dev.yml` never ran (no Compose plugin). Phoenix runs as container `da-agent-phoenix`.

## Waiting on the user

- **Days 7–11:** agree to add $5–10 for Haiku 5.5. **Before Day 20:** install the Compose plugin.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $0.31 (agent runs $0.06, spec proposals $0.25).

## Watch out

- A question uses 48–75k tokens, about $0.005; a runaway one 130k.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
- Answers have no row labels: ingestion needs our own labels, the score checks end to end. Keep the
  declarative spec (user, ADR 0006); a sandboxed code step is an open question.
- Scorer interpretations: a wrong `type` is wrong; table columns match by name only if all names match.
