# 0049: Garbled tool names are read back by code; a malformed call gets one retry in its turn

**Status:** Accepted, 2026-11-24.

**Context:** Groq checks tool calls on its side and answers 400 when the model's call does not
match the request's tools. With `gpt-oss-20b` this stopped 5 to 8 of 150 `regression` questions
per run, ended the demo video's chart run, and was the main reason the one-shot baseline abstained
(ADR 0048). The recorded cassettes held 107 such errors:

| Groq's message | Count |
|---|---|
| called `submit_answer<\|channel\|>commentary` | 65 |
| called `json` | 18 |
| output it could not parse, or arguments that are not JSON | 15 |
| called `run_sql` when it was not offered | 6 |
| called `answer` | 2 |
| `submit_plan` arguments did not match the schema | 1 |

Most are gpt-oss writing its own format tokens (`<|channel|>`) into the name of a tool it meant
to call. The error body carries the model's output as `failed_generation`, which the adapter
dropped. A malformed call cost a turn, and on the last turn it ended the run.

**Decision:**
- **Keep the output.** `InvalidToolCallError.failed_generation` holds up to 2,000 characters of
  it, and cassettes record and replay it. It is for diagnosis and never shown to the model, which
  gets a notice written by code.
- **Read back garbled names in the adapter.** When a name with `<|...` cut off and a
  `functions.` prefix removed is exactly a tool offered on this turn, and the arguments are a JSON
  object, the call is that tool's. The response says so (`repaired_tool_calls`, also on the LLM
  span). The arguments go through the tool's own checks, as any call's do. Nothing else is
  guessed: not `json`, not `answer`, and never a tool code withheld on this turn.
- **Retry once within the turn.** A malformed call is asked again at once, with the notice, and
  does not count as a turn. A second one in the same turn costs the turn as before, so the turn
  limit still bounds them, and on the last turn it still ends the run.
- The baseline gets the adapter's read-back, which is part of the LLM layer, and not the retry,
  which is agent behaviour.
- Rejected: retrying the identical request (temperature 0 gives the same output); offering every
  tool on the last turn (the model could keep exploring past the limit); a model call to fix the
  name (costs a call where code can decide).

**Consequences:**
- A read-back call reports no tokens: Groq returns no usage with the 400.
- Other providers do not return `failed_generation`, so nothing changes for them.
- Measured on `regression` (2026-11-24, one run each, $0.417 with the baseline): 64 calls read
  back, runs stopped on a malformed call 7 to 2, 50 fewer calls (6%), score +61
  to +67 (within noise). The baseline's rejected calls were `run_sql` on its answer turn, so it
  is unchanged (+40 to +42). Details in `docs/eval/README.md`.
- Cost and latency also fell in that run (15% and 22%), but the untouched baseline fell 7% and
  24% the same day: mostly Groq's load, not this change.
