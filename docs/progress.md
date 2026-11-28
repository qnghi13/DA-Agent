# Progress

Loaded into every session; keep it under 60 lines and replace stale content. **Last updated:** 2026-11-27 (layout layer)
**Phase:** product, deeper analysis for non-technical users, in order B -> C -> D (user,
2026-11-26): B fixed analyses (done), C insight cards, D multi-step deep analysis. The Zalo
challenge ended 2026-11-25: its limits no longer apply; its data stays private.
## Next step
- **General layouts** (ADR 0052, note 29, design + plan 2026-11-27) on branch
  `fix/ingest-multiline-cells`, nothing committed: crash fix for line breaks in cells, then the
  layout layer (grid regions and row roles, flagged sheet families, a `LayoutSpec` of fixed
  operators, coverage and shape checks, layout in `DatasetSpec.layout`, `DA_AGENT_INGEST_ROUTES`).
  `docs/eval/layout.md`: dev 30 -> 48/49 (`gpt-oss-120b`, twice); held-out once 33 -> 38/47
  ($0.15). 2026-11-28: the four held-out misses fixed on a synthetic upload (6/6, $0.03); dev
  still 48/49. Next: commit; a new unseen set to measure generality again.
- **Analysis tools B** (ADR 0051, note 28, design + plan 2026-11-26) on branch
  `feat/analysis-tools`: `analyze` with explain_change, trend, forecast, correlation,
  outliers computed in the sandbox; card, "Vì sao thay đổi?" button, drill chip in the web.
  Checked in the running app ($0.006). Eval ($0.042, 3 attempts): right kind 8/10 each,
  correct 6/8/7 of 10, controls 6/6 without analysis. `smoke` graph +17 off vs +14 on ($0.13,
  one run each: noise). Next: fix the eval's ambiguous drinks question, suggest dimensions to
  the model (it misses branch), then project C (insight cards). Known gaps: the design doc.
- **Dataset deletion** (ADR 0050, note 27, design doc): on `main`; checked in the
  running app (UI delete, a run stopped mid-call, rows and folders gone, spend and audit kept).
  Phoenix traces and golden rerun reports are not deleted (threat model).
- **Garbled tool calls** (ADR 0049): adapter reads back `X<|channel|>...`, one
  retry per turn. `regression` +61 -> **+67** (102/35/13, $0.280), stops 7 -> 2, 64 read back.
- **One-shot baseline** (ADR 0048): +40, then +42 after 0049 (its rejects are real `run_sql`
  wishes); gap to the agent is mostly coverage. ADR 0047 no harm. Open: `LIMIT 1` ties.
- **Vietnamese UI** on `main` (ADR 0046, note 25): Tiếng Việt option, typed
  dictionaries in `web/src/i18n`, API error `code`/`params`. Column descriptions stay English.
- **Answer language** on `main` (ADR 0047): a Vietnamese question gets `reason` and
  `clarify_question` in Vietnamese, decided by code. Add files: on `main` (ADR 0045).
0. Metric definitions on `main` (ADR 0036, note 16): saved from an answer's SQL, matched by
   code; eval +1 -> +7 (20 dev questions, $0.095). Phase 2, other readings with the answer, was
   built on a branch and removed (ADR 0037): `gpt-oss-20b` never flagged the ambiguity (4 real
   runs). Users: non-technical first; UI in English.
   Harness track done (ADRs 0038–0044, notes 17–23): manifest, record/replay, pass^k (one-run
   gains under ~9 points are noise), breaker, cancel and deadline, feedback, SLOs and alerts;
   later, an LLM judge checked against feedback.
1. v0.1.0 released (ADR 0028); holdout ran once (+44, never rerun). Open: an absent `product_id`
   guessed (run 97d7bc1f; a UI guardrail); `\uXXXX` escapes in SQL text (2 runs in all reports).
## Baselines (Groq `gpt-oss-20b`, before 559f88a): `regression` +53 (93/40/17), holdout +44.
## Done
- ADRs 0001–0052, notes 01–29. Scorer 3000/3000; splits in `eval/splits/splits.json`.
- Agent, sandbox (escape tests pass), backend over HTTP, reports; RAG cut (note 05).
  `datasets-dev/`: 24/24 ok, parse 99.98%+, exclusion precision and recall 100%.
## Not verified
- Pyright: 0 errors in src, eval, sandbox, scripts, and in CI (2026-11-23); tests not checked
  (184 errors, mostly fakes). No real call: Gemini, `openrouter`, `vllm` (serve.sh untested on a
  GPU). Postgres tests need `uv run --env-file .env pytest`.
## Waiting on the user
- No rows, questions, rulebook, or answers in any commit or image (organiser rules). When the
  challenge ends: delete `train/`, `eval/reports/`, and Docker volumes with eval data.
## Spend so far: about $4.42 (agent runs $3.55, spec proposals $0.57, layouts $0.21, demos $0.08).
- Open: Vietnamese chart requests drew a chart in 1 of 3 Groq runs (`make_chart` text is English).
## Watch out
- 30 questions on Groq cost about $0.05–0.10, and results vary run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
