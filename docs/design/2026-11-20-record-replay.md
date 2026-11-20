# Record and replay: rerun the agent without calling the model

**Status:** approved 2026-11-20. **ADR:** [0039](../adr/0039-record-replay.md).

## Why

Most optimisation changes sit after the model: verify, answer kind coercion, the DataMate adapter.
A real eval run measures them badly: it costs money, and the model answers a little differently
each run, so a change of a few points is lost in noise. The GPU run 1 analysis did a manual replay
(kind coercion +56 -> +84, $0). Record and replay makes that a tool: record the model's outputs
once, then rerun the same questions against them, so every difference comes from code.

## Goals and non-goals

- `eval/datamate/run.py --record` writes every model outcome of the run to `cassette.jsonl` in the
  output folder (gitignored, so no questions or rows are committed).
- `--replay DIR` reruns against `DIR/cassette.jsonl` without API keys and at $0. Replaying the same
  code gives the same answers.
- A request that is not in the cassette stops that question with an error that says at which
  message the request first differs from the closest recorded one (use 3: where a change diverges).
  `--replay-miss live` calls the real route instead and appends the result to the new cassette.
- `metrics.json` gets `replay`: source, hits, misses.
- Non-goals: measuring a new prompt (a prompt change misses on every question); model variance
  (pass^k, the next step); wiring into the worker or API; a CI cassette on `examples/` data (later).
- Anti-goals: an API key or URL in a cassette; replay spending money silently; a replay that
  differs from the original run with no code change.

## Approaches

| Option | Trade-off | Decision |
|---|---|---|
| A. Wrap `LLMAdapter` (`complete(request, model)`) | Provider-neutral, little code; retries, spans, and the ledger still run | **Chosen** by the user, 2026-11-20 |
| B. HTTP cassette (vcrpy, respx) | Per-SDK work (Gemini differs), headers carry keys, new dependency, keys on provider JSON bytes | Rejected |
| C. Resume from LangGraph checkpoints | Continues from one point; does not rerun later code against fixed model outputs | Rejected; that is time travel, step 4 |

Miss policy (chosen with A): strict by default, `--replay-miss live` as an option.

## Design

- **Key:** SHA-256 of canonical JSON (sorted keys) of provider, model, and the whole `LLMRequest`
  (messages, tools, max output tokens, temperature). `as_of` comes from the question, so dates do
  not break keys.
- **What is recorded:** successful responses, and the outcomes that depend on the request:
  `InvalidToolCallError` and `PermanentLLMError`. Rate limits and transient errors are network
  events and are not recorded; on replay the call simply succeeds.
- **Line format:** `{key, provider, model, outcome, response | error, messages}` where `messages`
  is the list of per-message hashes (role plus hash), small enough to find the divergence point.
- **Repeats:** the same key may occur several times in one run; outcomes replay in recorded order.
  More calls than recorded is a miss.
- **Miss report:** the recorded request with the longest common message-hash prefix (same provider
  and model) names the first differing message: index and role. `ReplayMissError` is a
  `PermanentLLMError`, so the agent ends the question with `llm_error` and the message.
- **Cost:** replayed responses keep their token usage (token budgets behave as in the original run)
  and carry `replayed=True`; `LLMClient` records them at $0. Live misses cost the real price.
- **Threads:** the cassette and recorder take a lock; eval workers share them.
- **Replay without keys:** strict replay builds routes from the `--routes` spec without adapters
  (`parse_route_spec`); live misses build the real routes.
- **Code:** `src/da_agent/llm/replay.py` (core, no DataMate code); wiring in `eval/datamate/run.py`.

## Left to the builder

File and function names beyond the above; the exact miss message wording.
