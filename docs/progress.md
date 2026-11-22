# Progress

Loaded into every session; keep it under 60 lines and replace stale content. **Last updated:** 2026-11-22 (Vietnamese UI)
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
- **Vietnamese UI (2026-11-22):** built on `feat/vietnamese-ui` (ADR 0046, note 25; not merged
  or pushed). English default, Tiếng Việt option next to the theme toggle; typed dictionaries in
  `web/src/i18n`; API errors carry `code`/`params`. Checked in the real UI (1 spec proposal, 1
  question on Groq). Found, not fixed: the agent's answer prose and the model-written column
  descriptions stay English for a Vietnamese question.
- Add files to a dataset: merged to `main` (ADR 0045, note 24). Open minors: ADR 0045 list.
0. Metric definitions on `main` (ADR 0036, note 16): saved from an answer's SQL, matched by
   code; eval +1 -> +7 (20 dev questions, $0.095). Phase 2, other readings with the answer, was
   built on a branch and removed (ADR 0037): `gpt-oss-20b` never flagged the ambiguity (4 real
   runs). Users: non-technical first; UI in English.
   Harness track done (ADRs 0038–0044, notes 17–23): manifest, record/replay, pass^k (one-run
   gains under ~9 points are noise), breaker, cancel and deadline, feedback, SLOs and alerts.
   Later: an LLM judge checked against feedback.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: run 97d7bc1f guessed
   an absent `product_id` (audit: a UI guardrail, not a score fix); `\uXXXX` escapes written into
   SQL text (2 runs in all reports).
## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done
- ADRs 0001–0046, notes 01–25. Scorer 3000/3000; splits in `eval/splits/splits.json`.
- Agent: `LLMClient`, SQL guard, LangGraph with checkpoints and verify, Groq `gpt-oss-20b`.
  Sandbox: all escape tests pass; ingestion and agent SQL run in it (ADRs 0009–0012).
  `datasets-dev/`: 24/24 ok, parse 99.98%+, exclusion precision and recall 100%.
- Backend (ADRs 0017–0022) over HTTP; reports; RAG cut (note 05).

## Not verified
- Pyright: 24 old errors. No real call: Gemini, `openrouter`, `vllm` (serve.sh untested on a
  GPU). Postgres tests need `uv run --env-file .env pytest`.

## Waiting on the user
- No rows, questions, rulebook, or answers in any commit or image (organiser rules). When the
  challenge ends: delete `train/`, `eval/reports/`, and Docker volumes with eval data.

## Spend so far: about $3.08 (agent runs $2.48, spec proposals $0.57, demos and e2e $0.011).

## Watch out

- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
