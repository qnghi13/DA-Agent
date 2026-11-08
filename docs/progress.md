# Progress

Loaded into every session, so keep it under 60 lines. Replace stale content instead of appending.
**Last updated:** 2026-11-09
**Phase:** Day 20b–c, sign-in and operations ([plan §6](plan.md#6-roadmap)), added 2026-11-09.

## Next step

1. Day 20 packaging done (ADR 0024, note 08): Compose, images, CI (never ran: no GitHub repo),
   demo data (June revenue 8,714,000). Day 20b–c done in code (ADRs 0025–0026, note 09): login
   links + `HttpOnly` cookie sessions, operator role, `admin login-link|revoke-sessions`,
   `DA_AGENT_PUBLIC_URL`, failed model attempts in `llm_calls.error_kind`,
   `jobs.started_at|finished_at`, `/v1/ops/summary|failures`, `/ops` page
   (`web/src/OpsDashboard.tsx`). Checked in Chromium (playwright-cli) at 1440 and 390, light and
   dark: link sign-in, invite, upload with the cookie, sign-out, spent link; this found and fixed
   a StrictMode race in sign-in. `deploy/smoke.sh` now signs the operator in; passed on a fresh
   Compose stack. Open: image publishing (CD).
2. Day 21. `regression` (150, ~$0.40) only at the end, if affordable.
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

- Before pushing: `gh` or a repo. **Before going public:** check the organizer's data rules, whether `cccd` and phone are fake, the
  licenses of skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far: about $1.89 (agent runs $1.45, spec proposals $0.44, demos $0.0025).
## Watch out

- A run of 30 questions costs about $0.05–0.10. `gpt-oss-20b` varies run to run: compare totals.
  Scorer: a wrong `type` is wrong; table columns match by name only if all names match.
- Do not regenerate `splits.json`, and never read data from its holdout shops. The scorer gets a
  holdout option on Day 21. `eval/reports/` is gitignored, so publish only aggregates in `docs/eval/`.
