# Progress

Loaded into every session through `CLAUDE.md`, so keep it under 60 lines. Update it when a task
finishes or a session ends. Replace stale content instead of appending. Git history keeps the rest.

**Last updated:** 2026-11-05
**Phase:** Days 1–2, Foundation ([plan §6](plan.md#6-roadmap)). Day 1 application work has not started.

## Next step

1. Ask whether to make the first commit. Nothing is committed yet, and `.agents/`, `.claude/skills/`
   symlinks, and `skills-lock.json` are the user's call to include.
2. Day 1 docs: `docs/architecture.md`, `docs/threat-model.md`, and the first ADRs, using
   [plan §2](plan.md#2-architecture).
3. Build the scorer test-first from `train/rulebook.md` §9, then the eval splits
   ([plan §3](plan.md#3-evaluation)).

## Done

- Dev environment: `CLAUDE.md`, Ruff, pytest, two hooks with tests, `tdd` and `agent-architecture`
  skills, three subagents. `uv run pytest` gives 70 passed and `ruff check` is clean.
- Plan approved and stored in [plan.md](plan.md).

## Not verified

- Subagents and skills are registered but have not been run.
- Whether `permissionMode: plan` actually blocks writes for `system-architect`.

## Waiting on the user

- **Before Day 2:** create free Groq and Google AI Studio keys.
- **Days 7–11:** agree to add $5–10 for Haiku 5.5. Claude gives notice first.
- **Before pushing to GitHub:** install `gh` or create the repo.
- **Before the repo goes public:** check the organizer's data rules and whether the `cccd` and phone
  columns are fake.

## Spend so far

$0. No real LLM calls have been made.

## Watch out

- `pyproject.toml` has `package = false` and `readme = "CLAUDE.md"`. Change both when `src/da_agent`
  and `README.md` are added.
- pyright is planned but not installed. Add it with the first application code.
- `command_guard` rejects mounting all of `train/`. The Runner must mount one shop directory.
- `agent-browser` also needs its CLI, installed globally with npm. The guard asks before that runs.

## Key answer facts

`train/answers.jsonl` has 3,000 rows: number 1884, percent 578, list 193, date 145, string 88,
unanswerable 70, boolean 23, table 19. `alias` is non-empty in 167 rows.
