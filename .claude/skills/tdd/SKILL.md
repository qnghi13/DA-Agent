---
name: tdd
description: Test-driven workflow for this repo. Use when adding behaviour or fixing a bug in Python code, including the scorer, parsers, SQL guard, tools, verification checks, and graph nodes. Skip for docs, config, and pure formatting.
---

# TDD

## Cycle

1. **Red:** write the smallest test that states the behaviour. Run it and confirm it fails for the
   expected reason. A failure from a typo or import error does not count.
2. **Green:** write the least code that passes.
3. **Refactor:** clean up with tests green.
4. Finish with `uv run pytest` and `uv run ruff check .`.

For a bug, first add a test that reproduces it.

## Commands

```bash
uv run pytest tests/path/test_x.py::test_name -x   # one test
uv run pytest                                      # everything except real LLM calls
uv run pytest -m integration                       # needs Docker or Postgres
uv run pytest -m security                          # boundary tests
```

## Test rules

- Tests are offline, deterministic, and free by default. Inject a fake `LLMClient` or use recorded
  responses. Mark a real call with `@pytest.mark.llm` and ask before running it.
- Assert on behaviour through public interfaces. Do not assert on prompt wording or private helpers.
- Use small inline fixtures or synthetic files. Do not read `train/` in unit tests, because public CI
  does not have it.
- Security boundaries get `@pytest.mark.security` tests that attempt the attack and prove it fails.

## What to test here

- **LLM behaviour:** parse and validate model output, then test how code handles bad output, refusals,
  and tool errors.
- **Graph nodes:** give input state and a fake LLM, then assert on the output state and route.
- **Parsers:** use real mess from the data, such as `"22,000"`, `25000k`, `1 hộp`, Excel serial dates,
  and title rows above headers.
- **Failure paths:** 429, timeout, tool crash, budget stop, resume after a kill.

Eval runs measure quality and are a separate step from tests. Never edit a test or weaken an assertion
just to get green. Report the conflict instead.
