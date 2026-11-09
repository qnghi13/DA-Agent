# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.
**Last updated:** 2026-11-10 (Day 22 committed, 3249037)
**Phase:** finishing: CI and release on GitHub, demo video ([plan §6](plan.md#6-roadmap)).

## Next step

1. Days 21–22 done: holdout once (+44; never rerun), `docs/eval/`, README diagrams; UI redesign
   (ADR 0027, note 10, `docs/images/`), smoke passes on the rebuilt `web` image. `/ops` bar charts
   with one bucket fill the width (data, not style).
2. CD (ADR 0028): tag `vX.Y.Z` → `release.yml` runs CI, pushes 4 images to GHCR with provenance
   and SBOM, then smoke-tests the pulled images. `compose.yml` reads `DA_AGENT_IMAGE`/`_TAG`;
   checked locally with GHCR-named images (smoke ok). Not yet run on GitHub.
3. User: push `main` (first CI run on GitHub), then tag `v0.1.0`; make the GHCR packages public
   once; demo video.
4. Later (optimisation, after finishing): run 97d7bc1f asked about a product not in the data
   ("trà đá"); the plan guessed `product_id='P06'` (Bánh flan) from the per-column value lists, no
   join by name, and verify passed (the number came from a query). Options: verify check that
   filter literals come from the question or a query result; prompt rule; then `bootstrap`.
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

- Days 1–19: ADRs 0001–0023, notes 01–07; Day 20: ADRs 0024–0026, notes 08–09. Scorer 3000/3000 on the real answers; splits in
  `eval/splits/splits.json` (24 dev, 6 holdout; `bootstrap`, `smoke`, `regression`).
- Agent: `LLMClient`, SQL guard, LangGraph with checkpoints and verify, Groq `gpt-oss-20b`.
  Sandbox: all escape tests pass; ingestion and agent SQL run in it (ADRs 0009–0012).
  `datasets-dev/`: 24/24 ok, parse 99.98%+, exclusion precision and recall 100%.
- Backend (ADRs 0017–0022) end to end over HTTP ($0.0014); reports ($0.017); RAG cut (note 05).

## Not verified

- Pyright: 24 old errors (sqlglot types in verify, data_checks, graph). Gemini, `openrouter`: no real call.
  Postgres tests: `uv run --env-file .env pytest` (`DA_AGENT_TEST_POSTGRES_URL` is in `.env`).
- One hook test (deleting the home parent) assumes the repo is under /home, as on GitHub runners.

## Waiting on the user

- Repo and GHCR packages public (2026-11-10). Skill licenses MIT/Apache-2.0, credited in
  `THIRD_PARTY_NOTICES.md`. Organizer rules (§4): no rows, questions, rulebook, or answers in any
  commit or image; README and `docs/eval/` now cite challenge.zalo.ai. Open for the user: model
  calls sent cell values (customer names not hidden) to Groq; delete `train/`, `eval/reports/`,
  and the Docker volumes with eval data when the challenge ends.

## Spend so far: about $2.59 (agent runs $2.13, spec proposals $0.46, demos $0.0025).
## Watch out

- A run of 30 questions costs about $0.05–0.10. `gpt-oss-20b` varies run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops: the one
  holdout run is done. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
