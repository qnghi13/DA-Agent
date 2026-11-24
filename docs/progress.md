# Progress

Loaded into every session; keep it under 60 lines and replace stale content. **Last updated:** 2026-11-24 (dataset deletion)
**Phase:** optimisation. Challenge rules: ≤15B params in total, offline Docker, 2×5090, 6 h,
re-run must match ±0.03 PA; Groq `gpt-oss-20b` (21B, API) is not allowed for the submission.

## Optimisation (2026-11-11)
- `final-regression` failures (57): rules 19, periods 7, shorthand 7, harness 17, other 7.
- Done (559f88a): code-computed periods, Vietnamese shorthand and Telex expansion, `vllm:` route.
  Test shops are new and rulebooks differ per split: ingestion runs in the container; the
  semantic layer holds data facts only, never rules.
- Public test (`public_test/`, 14 shops): 14/14 built; spec repairs in code; ADR 0029.
- GPU kit (a24a501): runbook, `serve.sh`, preflight, `--workers`.
- GPU run 1 (2026-11-13, 1x5090, vLLM 0.33.0, `gemma-4-12B-it` bf16, batch-invariant): preflight
  ok (tool calls, identical alone vs busy). `regression` on `datasets-dev`, 3 workers: **+56**
  (99/43/8) vs Groq +53; ~12.7 s/question wall. Failure analysis ($0): 14 right values with the
  wrong kind (percent as number, date as string): offline kind coercion gives +84, hurts none
  (Groq +55). Then status variants and line-vs-header revenue (~12, checked by hand SQL), debt
  4, budget abstains 8 (SQLite functions, made-up columns). Semantic layer: private folder.
## Next step
- **Dataset deletion** (ADR 0050, note 27, design doc): on `main` (not pushed);
  real-app check pending (migration 0018; delete from the UI with a run in flight). Phoenix
  traces and golden rerun reports are not deleted (threat model).
- **Garbled tool calls** (ADR 0049): adapter reads back `X<|channel|>...`, one
  retry per turn. `regression` +61 -> **+67** (102/35/13, $0.280), stops 7 -> 2, 64 read back.
- **One-shot baseline** (ADR 0048): +40, then +42 after 0049 (its rejects are real `run_sql`
  wishes); gap to the agent is mostly coverage. ADR 0047 no harm. Open: `LIMIT 1` ties.
- **Vietnamese UI** on `main` (ADR 0046, note 25; not pushed): Tiếng Việt option, typed
  dictionaries in `web/src/i18n`, API error `code`/`params`. Column descriptions stay English.
- **Answer language** on `main` (ADR 0047): a Vietnamese question gets `reason` and
  `clarify_question` in Vietnamese, decided by code. Add files: on `main` (ADR 0045).
0. Metric definitions on `main` (ADR 0036, note 16): saved from an answer's SQL, matched by
   code; eval +1 -> +7 (20 dev questions, $0.095). Phase 2, other readings with the answer, was
   built on a branch and removed (ADR 0037): `gpt-oss-20b` never flagged the ambiguity (4 real
   runs). Users: non-technical first; UI in English.
   Harness track done (ADRs 0038–0044, notes 17–23): manifest, record/replay, pass^k (one-run
   gains under ~9 points are noise), breaker, cancel and deadline, feedback, SLOs and alerts.
   Later: an LLM judge checked against feedback.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: an absent `product_id`
   guessed (run 97d7bc1f; a UI guardrail); `\uXXXX` escapes in SQL text (2 runs in all reports).
## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done
- ADRs 0001–0050, notes 01–27. Scorer 3000/3000; splits in `eval/splits/splits.json`.
- Agent, sandbox (escape tests pass), backend over HTTP, reports; RAG cut (note 05).
  `datasets-dev/`: 24/24 ok, parse 99.98%+, exclusion precision and recall 100%.
## Not verified
- Pyright: 0 errors in src, eval, sandbox, scripts, and in CI (2026-11-23); tests not checked
  (184 errors, mostly fakes). No real call: Gemini, `openrouter`, `vllm` (serve.sh untested on a
  GPU). Postgres tests need `uv run --env-file .env pytest`.
## Waiting on the user
- No rows, questions, rulebook, or answers in any commit or image (organiser rules). When the
  challenge ends: delete `train/`, `eval/reports/`, and Docker volumes with eval data.
## Spend so far: about $4.02 (agent runs $3.38, spec proposals $0.57, demos and e2e $0.05).
- Demo video (2026-11-22, not in the repo): re-record once open items are fixed.
## Watch out
- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
