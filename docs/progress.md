# Progress

Loaded into every session through `CLAUDE.md`, so keep it under 60 lines. Update it when a task
finishes or a session ends. Replace stale content instead of appending. Git history keeps the rest.

**Last updated:** 2026-11-05
**Phase:** Days 1–2, Foundation ([plan §6](plan.md#6-roadmap)). Design docs are done. No application
code exists yet.

## Next step

1. Ask whether to commit the design docs and project structure. The user's commit `2c3e6ad` holds the
   dev environment, and there is no remote yet.
2. Build the scorer test-first from `train/rulebook.md` §9, then the eval splits
   ([plan §3](plan.md#3-evaluation)).
3. Day 2: `LLMClient`, tracing, cost ledger, minimal agent on biz_123, `smoke` run. Ask before the run.

## Done

- Dev environment: `CLAUDE.md`, Ruff, pytest, two hooks with tests, `tdd` and `agent-architecture`
  skills, three subagents.
- Docs: [plan](plan.md), [architecture](architecture.md), [threat model](threat-model.md),
  [7 ADRs](adr/README.md), first learning note, README, LICENSE.
- Project structure: empty directories with `.gitkeep`. No Python packages yet.

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
