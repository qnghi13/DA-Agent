# Progress

Loaded into every session; keep it under 60 lines and replace stale content. **Last updated:** 2026-11-14 (result export, ADR 0032)
**Phase:** optimisation. Challenge rules: ≤15B params in total, offline Docker, 2×5090, 6 h,
re-run must match ±0.03 PA; Groq `gpt-oss-20b` (21B, API) is not allowed for the submission.

## Optimisation (2026-11-11)
- Failure analysis of `final-regression` (57 not correct): business-rule misuse 19, period
  arithmetic 7, Vietnamese shorthand 7, unanswerable confusion 3, wrong type 3, harness 17
  (budget 9, Groq tool-call 400s 5). Hand SQL on `biz_108` and four period questions gives the
  exact answers: ingestion is not the cause there.
- Done (559f88a): code-computed periods, Vietnamese shorthand and Telex expansion, `vllm:` route.
  Test shops are new and rulebooks differ per split: ingestion runs in the container; the
  semantic layer holds data facts only, never rules.
- Public test (`public_test/`, gitignored, 14 shops): `prepare --all-shops` on Groq, $0.10 total,
  14/14 built. Spec repairs in code: raw text as a clean name becomes its slug; rules naming an
  undeclared column by its slug map back to it (dev_013 dates 63% -> 100%). ADR 0029: code
  excludes proven subtotals (dev_010 narrow rule: 240 left -> 0; train exclusions still 100%).
- GPU kit (a24a501): runbook, `serve.sh`, preflight, `--workers`. Memory: ADR 0030 (9806563).
- GPU run 1 (2026-11-13, 1x5090, vLLM 0.33.0, `gemma-4-12B-it` bf16, batch-invariant): preflight
  ok (tool calls, identical alone vs busy). `regression` on `datasets-dev`, 3 workers: **+56**
  (99/43/8) vs Groq +53; ~12.7 s/question wall. Failure analysis ($0): 14 right values with the
  wrong kind (percent as number, date as string): offline kind coercion gives +84, hurts none
  (Groq +55). Then status variants and line-vs-header revenue (~12, checked by hand SQL), debt
  4, budget abstains 8 (SQLite functions, made-up columns). Semantic layer: private folder.
## Next step
0. Result export (ADR 0032, `feat/result-export` on `main`, not merged): full CSV per query in a
   sandbox, chart PNG/SVG ($0.001 browser check). Then merge and `docker compose up -d --build`.
   Guard signals (ADR 0031): the next eval run reports `verify_kinds`, `runs_saved_by_repair`.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: run 97d7bc1f guessed
   a `product_id` absent from the data. Offline audit of `final-regression` (2026-11-13, $0):
   no wrong answer had an absent filter literal; absent ones sit in exploratory queries of
   correct runs. A literal check is a UI guardrail, not a score fix. One abstain: `\uXXXX`
   escapes written into SQL text (2 runs in all reports).

## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done

- ADRs 0001–0032, notes 01–12. Scorer 3000/3000; splits in `eval/splits/splits.json`.
- Agent: `LLMClient`, SQL guard, LangGraph with checkpoints and verify, Groq `gpt-oss-20b`.
  Sandbox: all escape tests pass; ingestion and agent SQL run in it (ADRs 0009–0012).
  `datasets-dev/`: 24/24 ok, parse 99.98%+, exclusion precision and recall 100%.
- Backend (ADRs 0017–0022) end to end over HTTP ($0.0014); reports ($0.017); RAG cut (note 05).

## Not verified
- Pyright: 24 old errors. No real call: Gemini, `openrouter`, `vllm` (serve.sh untested on a
  GPU). Postgres tests need `uv run --env-file .env pytest`.

## Waiting on the user
- No rows, questions, rulebook, or answers in any commit or image (organiser rules). When the
  challenge ends: delete `train/`, `eval/reports/`, and Docker volumes with eval data.

## Spend so far: about $2.73 (agent runs $2.15, spec proposals $0.56, demos $0.0035).

## Watch out

- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
