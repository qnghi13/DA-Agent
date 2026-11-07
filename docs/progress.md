# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.
**Last updated:** 2026-11-06
**Phase:** Days 3–6, Data and tools ([plan §6](plan.md#6-roadmap)). Baseline B4 +13; ADR 0012 work uncommitted.

## Next step

1. Abstentions (11/30 on B4): 6 iteration limits, 4 malformed calls (`functions.submit_answer`),
   ~16 binder errors from mistyped names (`PhươngThucTT`). Wrong: 3 (debt, expiry vs today, a
   misread channel). Look at these as general patterns only.
2. Error patterns on the 5 subtotal shops (9–11 wrong of 30), then the Foundation learning note.

## Baselines (`bootstrap`, biz_123, 30 questions; correct / wrong / abstained, score)

- A `pos.sqlite` 3/8/19 −5. A2 + turn notices 2/11/17 −9. B sandbox 3/14/13 −11. B2 + known
  values 6/16/8 −10. B3 + source names 6/15/9 −9. **B4 + dedupe and text-match hint 16/3/11 +13.**
  Runs in `eval/reports/baseline-*`; dev answers read only for error patterns (user, 2026-11-07).
- `regression`, 5 subtotal shops: C with exclusion 13/9/8 +4, D without 16/11/3 +5: no measurable
  effect. In 4 shops subtotal rows have no date, so date filters already drop them; only biz_107
  fills dates down onto them.
## Done

- Dev environment, design docs, ADRs 0001–0011, README, LICENSE. Scorer (`eval/scorer/`, rulebook §7, §9): 3000/3000 on the real answers. Splits in
  `eval/splits/splits.json`: 24 dev, 6 holdout shops; sets `bootstrap`, `smoke`, `regression`.
- Minimal agent: `LLMClient`, SQL guard, bounded tool loop, tracing, resumable `eval/datamate`
  runner. Model: Groq `gpt-oss-20b`; `search_rules` (ADR 0008).
- Runner and sandbox image: all escape tests pass on real containers. Raw ingestion in the sandbox
  (ADR 0009). `DatasetSpec` pipeline: LLM proposes, sandbox builds `dataset.duckdb` with
  `_quarantine` and parse-rate feedback. Outputs in `eval/reports/` (gitignored).
- `exclude_rows` (ADR 0010): rules, `_excluded`, rare shapes with sums, and checks for kept
  subtotals and for rules that remove records (full rows, identifiers, share). Rules still failing
  after the last attempt are dropped. Measured with `eval/ingestion/` (labels, synthetic, scorer).
  `datasets-dev/` (prepare-v4, biz_109 and biz_124 from v4b): 24/24 ok, parse 99.98%+, exclusion
  precision and recall 100% on 2,032 labelled rows.
- Agent SQL in the sandbox (ADR 0011): DuckDB guard, locked read-only engine, `SqlRun` per query
  (~0.7 s), `SandboxDataset`, `open_prepared_shop`, `run --datasets-dir/--shops`. `calendar.csv`
  travels with each upload; `prepare --reuse-specs` rebuilds without the model. `datasets-dev/`
  has the calendar; `datasets-dev-noexclude/` is the same without exclusion; `-v1`/`-v2` are older.
- Loop: turn notice at 3 left, last turn offers only `submit_answer`; Groq `failed_generation` is
  a malformed call. ADR 0012: source column names kept, known values listed, exact repeats removed
  when a 1–2 column key proves them (4 dev shops; 3 keyless logs kept). Results keep tool errors.

## Not verified

- Gemini: no real call. Ollama too slow. `compose.dev.yml` never ran. Phoenix: `da-agent-phoenix`.

## Waiting on the user

- Days 7–11: $5–10 for Haiku 5.5. Before Day 20: Compose plugin. Before pushing: `gh` or a repo. **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $1.16 (agent runs $0.72, spec proposals $0.44).
## Watch out

- A run of 30 questions costs about $0.05–0.10. `gpt-oss-20b` varies run to run: compare totals.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
- Scorer interpretations: a wrong `type` is wrong; table columns match by name only if all names match.
