# Progress

Loaded into every session; keep it under 60 lines and replace stale content. **Last updated:** 2026-11-20 (record/replay)
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
- Public test (`public_test/`, 14 shops): `prepare --all-shops` on Groq $0.10, 14/14 built;
  spec repairs in code (dev_013 dates 63% -> 100%); ADR 0029 excludes proven subtotals.
- GPU kit (a24a501): runbook, `serve.sh`, preflight, `--workers`. Memory: ADR 0030 (9806563).
- GPU run 1 (2026-11-13, 1x5090, vLLM 0.33.0, `gemma-4-12B-it` bf16, batch-invariant): preflight
  ok (tool calls, identical alone vs busy). `regression` on `datasets-dev`, 3 workers: **+56**
  (99/43/8) vs Groq +53; ~12.7 s/question wall. Failure analysis ($0): 14 right values with the
  wrong kind (percent as number, date as string): offline kind coercion gives +84, hurts none
  (Groq +55). Then status variants and line-vs-header revenue (~12, checked by hand SQL), debt
  4, budget abstains 8 (SQLite functions, made-up columns). Semantic layer: private folder.
## Next step
0. Metric definitions on `main` (ADR 0036, note 16): saved from an answer's SQL, matched by
   code; eval +1 -> +7 (20 dev questions, $0.095). Phase 2, other readings with the answer, was
   built on a branch and removed (ADR 0037): `gpt-oss-20b` never flagged the ambiguity (4 real
   runs). Users: non-technical first; UI in English.
   Harness track (user, 2026-11-19: learn the parts around the agent, not features). Done: run
   manifest (ADR 0038, note 17); record/replay (ADR 0039, note 18: 3 Groq questions replayed
   17/17, same answers, $0.012). Next: pass^k; circuit breaker, outbound rate limit, idempotent
   calls on resume; cancel, deadline, time travel; feedback to dataset, online eval; alerts, SLOs.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: run 97d7bc1f guessed
   an absent `product_id` (audit: a UI guardrail, not a score fix); `\uXXXX` escapes written into
   SQL text (2 runs in all reports).
## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done
- ADRs 0001–0039, notes 01–18. Scorer 3000/3000; splits in `eval/splits/splits.json`.
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

## Spend so far: about $2.85 (agent runs $2.26, spec proposals $0.57, demos $0.006).

## Watch out

- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
