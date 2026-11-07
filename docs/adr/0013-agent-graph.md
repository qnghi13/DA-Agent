# 0013: The agent graph: one plan call, per-turn checkpoints, and code verification

**Status:** Accepted, 2026-11-07

**Context:** The minimal agent was one bounded tool loop in a function. It could not pause to ask the
user, could not survive a killed worker, and returned whatever the model submitted. ADR 0001 chose
LangGraph with a Postgres checkpointer; this ADR records how the graph is cut into nodes.

**Decision:**
- `plan` is one model call that reads the question, the documents, and the schema, and returns
  steps, whether the question is ambiguous, and a question for the user. It replaces the separate
  `understand` and `plan` nodes of the first design, which would read the same input twice. A
  failed or malformed plan never stops the run: the tool loop starts without one.
- `clarify` interrupts only in interactive runs, and at most once. Eval runs have no one to ask, so
  the plan follows the documents' defaults instead.
- The tool loop is two nodes, `agent` (one model turn) and `tools` (run that turn's calls). Each
  turn is a checkpoint, so a resumed run repeats at most the turn that was running. Tools only
  read, so repeating one is harmless.
- `verify` runs code checks on the submitted answer: at least one successful query, and every
  number in the answer within rounding of a number in a query result (a ratio may be given as
  percent points). A failure goes back to the loop with the reason, at most twice, and then the
  answer is withheld. Checks on periods, joins, and recomputation come in the reliability phase.
- State holds plain data only: the question, plan, messages, steps, counters, and the answer. The
  dataset, model client, and tracer are runtime context. A provider failure leaves the run open at
  its last checkpoint, so a retry continues it instead of reading back a failure.
- Malformed tool calls cost a turn but not a nudge, so the turn limit bounds them.

**Consequences:**
- A killed run continues in another process without repeating finished model turns. Tests prove it
  in memory and on Postgres.
- One extra model call per question for the plan, offset by giving the loop the schema up front.
- A correct answer computed outside SQL is withheld, which costs points when the arithmetic was
  right. The system prompt already requires every number to come from a query.
- Saved runs are keyed by thread id; eval runs use the output directory and question id, so
  rerunning into the same directory reads back the saved answers.

**Rejected:**
- Separate `understand` and `plan` calls: twice the input tokens for one decision.
- The whole tool loop as one node: simpler, but a resume would repeat every model turn.
- Routing on the plan's "missing data" judgment: a guess made before any query would abstain on
  answerable questions. The loop checks the data and may answer `unanswerable` itself.
- An LLM critic in `verify`: see ADR 0007.
