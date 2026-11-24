# 0048: A one-shot text-to-SQL baseline measures what the agent adds

**Status:** Accepted, 2026-11-24.

**Context:** The eval reports the agent's score (+53 on `regression`) and how changes moved it,
but not what a plain text-to-SQL prompt scores on the same questions. Without that floor, a
reader cannot tell whether the tool loop, verification, and repair earn their cost. The earlier
"tool loop" runs (A and B in `docs/eval/README.md`) are agents too: they explore, retry, and
answer over many turns.

**Decision:**
- `eval/baselines/oneshot.py`, run with `--agent oneshot`, makes two model calls. First, the model
  reads the schema (the same compact form `describe_dataset` returns), every document in full, the
  date, and the question, and calls `run_sql` once. Code runs the query. Second, the model sees the
  result and calls `submit_answer`.
- **Same inputs as the agent:** prepared datasets in the sandbox, the same documents, the same
  `run_sql` and `submit_answer` specs and dialect hints, and the same answer kinds, so the
  baseline loses nothing to answer format. Rules the agent fetches with `search_rules` are in the
  baseline's prompt in full: it has no tool to fetch them.
- **Left out, because the agent adds them:** exploration and more than one query, rule search,
  the agent's system prompt, the period and shorthand notes on the question, retrying a failed
  query, nudges after a turn without a tool call, the plan, code verification, and repair.
- A turn without the expected call, a malformed call, or an invalid answer ends the question
  unanswered (stop reasons `no_tool_call`, `invalid_tool_call`, `answer_format`).
- It lives in `eval/`, not `src/`: the service never runs it.
- Rejected: one call that returns SQL only, with code turning the result into an answer. Most
  DataMate answers need a kind (percent, date, list) that a result table does not carry, so that
  baseline would mostly measure formatting.

**Consequences:**
- A question costs two calls and about 13,000 tokens, most of them the rulebook.
- First run (2026-11-24, `regression`, $0.148): +40 (76 / 36 / 38) against the agent's +53. As
  accurate when it answers; most abstentions are an answer turn where the model wanted another
  query. Details in `docs/eval/README.md`.
- The agent's published score comes from older code (`final-regression`, 2026-11-09). The gap
  between the two is the headline number, so it should be large next to run-to-run noise before
  it is called a result: two single runs of 150 questions differ by about 10 points (one standard
  deviation) by chance, scaled from the pass^k runs of ADR 0040.
