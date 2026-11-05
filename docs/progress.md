# Progress

Loaded into every session through `CLAUDE.md`, so keep it under 60 lines. Update it when a task
finishes or a session ends. Replace stale content instead of appending. Git history keeps the rest.

**Last updated:** 2026-11-05
**Phase:** Days 1–2, Foundation ([plan §6](plan.md#6-roadmap)). Day 1 is done. No code under `src/`
exists yet.

## Next step

1. Ask whether to commit the scorer and splits. They are uncommitted.
2. Day 2: `LLMClient`, tracing, cost ledger, minimal agent on biz_123. It needs the user's free API
   keys. Use the `agent-architecture` skill first.
3. Run the `bootstrap` question set (30 questions, biz_123 only). Ask before the run.

## Done

- Dev environment in `.claude/`, with Ruff, pytest, hooks, skills, and subagents.
- Design docs, 7 ADRs, README, LICENSE, and the project structure (`.gitkeep` until modules arrive).
- Scorer: `eval/scorer/`, implementing rulebook §9. On the real answers, correct answers score
  3000/3000, the same through aliases, empty scores 0, and all perturbed answers score -3000.
- Splits: `eval/splits/splits.json` with 24 dev and 6 holdout shops, and the question sets `bootstrap`
  (30), `smoke` (30), `regression` (150). Run with
  `uv run python -m eval.scorer.cli --answers A --submission S --splits eval/splits/splits.json --set smoke`.

## Decisions needed

- **By Day 3:** should file parsing run in a sandbox? See
  [architecture open questions](architecture.md#open-questions). Other open questions are listed there.

## Not verified

- Subagents and skills are registered but have not been run.
- Whether `permissionMode: plan` actually blocks writes for `system-architect`.
- All security controls in the threat model are planned, none implemented.

## Waiting on the user

- **Before Day 2:** create free Groq and Google AI Studio keys.
- **Days 7–11:** agree to add $5–10 for Haiku 5.5. Claude gives notice first.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before the repo goes public:** check the organizer's data rules and whether the `cccd` and phone
  columns are fake. Review the licenses of the skills in `.agents/`, and the `brainstorming` scripts.

## Spend so far

$0. No real LLM calls have been made.

## Watch out

- `pyproject.toml` has `package = false`. Remove it and add a build setup when the first module in
  `src/da_agent` is added. pyright is planned but not installed, so add it then too.
- `command_guard` rejects mounting all of `train/`. The Runner must mount one shop directory.
- `agent-browser` also needs its CLI, installed globally with npm. The guard asks before that runs.
- `eval/reports/` is gitignored. Publish only aggregate results in `docs/eval/`.
- Do not regenerate `splits.json` without a reason, because it invalidates baselines. Never read data
  from the holdout shops listed there. The scorer has no holdout set yet: add that on Day 21.
- Scorer interpretations the rulebook leaves open: a wrong `type` is wrong, `unanswerable` needs a
  non-empty `reason`, and table columns match by name only when all names match.
