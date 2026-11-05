# DA-Agent

Open-source agent that analyses any uploaded CSV/Excel/SQLite data. Learning and portfolio project
for an AI Engineer role: architecture, reliability, and operability matter more than score tuning.

## Current state

@docs/progress.md

The approved plan is in `docs/plan.md`. Read only the section a task needs, using its table at the
top. Update `docs/progress.md` when a task finishes or a session ends.

## Commands

```bash
uv sync                   # install dev dependencies
uv run pytest             # tests, excludes real LLM calls
uv run pytest -m llm      # real LLM calls: costs money or quota, ask first
uv run ruff check .       # lint
uv run ruff format .      # format
```

## Stack

Python 3.12, uv, FastAPI, Pydantic, LangGraph (Postgres checkpointer), DuckDB, sqlglot, Vega-Lite,
React + Vite + TypeScript, OpenTelemetry + Phoenix, Docker Compose.

## Rules

- **Language:** reply to the user in Vietnamese. Code, README, and architecture docs are in English.
  Learning notes in `docs/learning/` are in Vietnamese.
- **Git:** commit or push only when the user asks.
- **Dependencies:** add only what the current task needs. Project skills are pinned in
  `skills-lock.json`. Ask before adding a plugin or skill.
- **Approved work:** do not redo design or brainstorming for what `docs/plan.md` already decides. Use
  `brainstorming` only for what it leaves open.
- **Tests first:** new behaviour starts with a failing test. Use the `tdd` skill.
- **Agent design:** use the `agent-architecture` skill before changing the graph, state, tools, LLM
  layer, ingestion, or sandbox.

## Data

- `train/` is read-only and gitignored. Never commit, move, or edit it.
- Never expose `train/answers.jsonl` to the agent or sandbox. Only the scorer reads it.
- The core has no DataMate-specific code. DataMate exists only as an eval adapter.
- Holdout shops run once, at the end. Do not inspect them to tune behaviour.

## Spend

Ask before running anything that calls a real LLM on more than a handful of questions. Report the
cost of every eval run.
