# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.
**Last updated:** 2026-11-08
**Phase:** Days 16–17, Backend ([plan §6](plan.md#6-roadmap)). Days 14–15 closed (note 05).

## Next step

1. Backend: job queue and migrations done (ADR 0017, `storage/jobs.py`). Next: worker (claims,
   heartbeats, runs the graph with the job id as thread), then API with keys, SSE, isolation.
   Charts and reports done (ADRs 0015–0016); `--report` writes HTML (`report-sample`, $0.017).
   RAG experiment cut: plan with titles only +2 vs all rules +11 already answers it (note 05).
2. `regression` (150, ~$0.40) only at the end, if affordable.
## Baselines (`bootstrap`, biz_123, 30 questions; correct / wrong / abstained, score)

- A −5; B sandbox −11; **B4 dedupe +13**. `eval/reports/baseline-*`; dev answers for patterns only.
- Row descriptions in `describe_dataset` and status guidance in the prompt: C2 18/8/4 +10 (from
  +4), B5 18/4/8 +14 (from +13). `smoke` (19 shops) S1 13/6/11 +7, $0.06: Days 3–6 closed.
- Graph (`--agent graph`, plan reads all rules): B7 18/7/5 +11, C4 16/7/7 +9, S3 18/7/5 +11 = +31,
  same total as the loop (+31), ~13% more tokens. Plan with rule titles only gave +2 on bootstrap.
- Verify checks replayed on saved graph runs flag 0 correct answers: period, fan-out, unreadable
  values (ADR 0014), typed inputs (12 literal-only answers, 7 correct, inputs all from data).
  B8 with all checks: 17/7/6 +10 vs B7 +11, $0.11; no new check fired: noise.
## Done

- Dev environment, design docs, ADRs 0001–0017, README, LICENSE. Scorer (`eval/scorer/`, rulebook §7, §9): 3000/3000 on the real answers. Splits in
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
- Agent SQL in the sandbox (ADR 0011): DuckDB guard, read-only engine, a container per query
  (~0.7 s), `run --datasets-dir/--shops`. `prepare --reuse-specs` rebuilds without the model.
- Loop: turn notice at 3 left, last turn offers only `submit_answer`; Groq `failed_generation` is
  a malformed call. ADR 0012: source column names kept, known values listed, exact repeats removed
  when a 1–2 column key proves them (4 dev shops; 3 keyless logs kept). Results keep tool errors.

## Not verified

- Gemini and `openrouter` (needs credits): no real call. `compose.dev.yml` never ran. Postgres:
  `da-agent-postgres` on 127.0.0.1:5433; set `DA_AGENT_TEST_POSTGRES_URL` in the same command.

## Waiting on the user

- Before Day 20: Compose plugin. Before pushing: `gh` or a repo. **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $1.89 (agent runs $1.45, spec proposals $0.44).
## Watch out

- A run of 30 questions costs about $0.05–0.10. `gpt-oss-20b` varies run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
