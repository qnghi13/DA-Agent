# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.
**Last updated:** 2026-11-12 (memory backend, ADR 0030)
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
- GPU kit (2026-11-12, uncommitted): `docs/eval/gpu.md` runbook, `scripts/gpu/serve.sh` (vLLM
  on the box, tunnel to the dev machine), `eval.gpu.preflight` (tool call, batch-invariant
  output, throughput), `run --workers N` and `--all-questions`, `VLLM_EXTRA_BODY`,
  `VLLM_MIN_OUTPUT_TOKENS`. Groq smoke: 4 questions, 2 workers, ok ($0.016 with preflight).
- Next: rent a GPU; rerun prepare + `regression` with 2–3 ≤15B models. Then a
  per-shop semantic layer (canonical order status, revenue lines, base-unit qty) in a private,
  uncommitted folder (user, 2026-11-11), ignored via `.git/info/exclude`. Test size: from user.

## Next step
0. Memory (ADR 0030, uncommitted): backend done; next web UI for notes, then learning note 11.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: run 97d7bc1f guessed
   a `product_id` absent from the data; option: verify that filter literals come from a query.

## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done

- Days 1–19: ADRs 0001–0023, notes 01–07; Day 20: ADRs 0024–0026, notes 08–09. Scorer 3000/3000 on the real answers; splits in
  `eval/splits/splits.json` (24 dev, 6 holdout; `bootstrap`, `smoke`, `regression`).
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

## Spend so far: about $2.72 (agent runs $2.14, spec proposals $0.56, demos $0.0025).

## Watch out

- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
