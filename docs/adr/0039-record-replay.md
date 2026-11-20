# 0039: Eval runs can record model outcomes and replay them at no cost

**Status:** Accepted, 2026-11-20. Design: [record and replay](../design/2026-11-20-record-replay.md).

**Context:** Most optimisation changes sit after the model (verify, answer kind coercion, the
DataMate adapter). A real eval run measures them badly: it costs money, and the model answers a
little differently every run, so a change of a few points is lost in noise. The GPU run 1 analysis
replayed outputs by hand to measure kind coercion.

**Decision:**
- `da_agent.llm.replay` wraps `LLMAdapter`. `RecordingAdapter` appends each outcome to a JSONL
  cassette; `ReplayAdapter` answers from it. The key is SHA-256 over the canonical JSON of the
  provider, model, and whole `LLMRequest`.
- Recorded outcomes are responses and the errors that depend on the request
  (`InvalidToolCallError`, `PermanentLLMError`). Rate limits and transient errors are network
  events and are not recorded. Error text is stored with URLs and key-like tokens removed.
- Eval wraps each question in `replay_scope(question_id)`: lines name the question and attempt, so
  two questions with the same request keep their own outcomes, and a question retried after a
  failure replays its latest attempt first (earlier attempts stay behind it for turns only they
  recorded, as when a checkpointed run resumed). Within one attempt, outcomes replay in order.
- With several routes, a route whose request a later route has recorded steps aside, so the
  fallback of the recorded run is followed and no model is called for it.
- A miss raises `ReplayMissError` (a `PermanentLLMError`) naming the first message where the request
  differs from the closest recorded one of the same route. `--replay-miss live` calls the model
  instead and records the result.
- Replayed responses keep their token usage, so token budgets act as in the recorded run, and carry
  `replayed=True`, which `LLMClient` prices at $0.
- `eval/datamate/run.py`: `--record` writes `cassette.jsonl` to the output folder; `--replay DIR`
  reads `DIR/cassette.jsonl`, needs no API key in stop mode, and writes hits and misses into
  `metrics.json`. A miss leaves the question open, like a provider failure, but never counts toward
  stopping the run.

**Rejected:**
- An HTTP cassette (vcrpy, respx): work per SDK, headers carry keys, keys depend on provider JSON.
- Resuming LangGraph checkpoints: continues from one point; does not rerun later code against fixed
  model outputs. That is time travel, a later step.

**Consequences:**
- A cassette holds questions and data the model saw, so it stays in gitignored `eval/reports/`.
  A committed CI cassette needs synthetic `examples/` data; not built.
- Any change to what the model sees (prompt, tool spec, a tool result that differs) misses from that
  turn on. Replay measures code after the model; a new prompt needs a real run.
- Money budgets (`max_cost_usd`) do not act as in the recorded run, since replay costs $0. The eval
  uses token budgets, which do.
- A question that failed on every route when recorded has no outcome and misses on replay.
- `metrics.json` `replay` counts cover this invocation only; a resumed replay counts only the
  questions it ran. A miss is counted once per question and request.
- The key covers every field of `LLMRequest`, `Message`, and `ToolCall`, defaults included, so adding
  a field invalidates old cassettes; they then miss with "tools or options differ".
