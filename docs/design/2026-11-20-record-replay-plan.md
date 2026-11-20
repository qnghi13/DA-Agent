# Record and replay Implementation Plan

**Goal:** record model outcomes of an eval run and replay them at $0 (design:
[2026-11-20-record-replay.md](2026-11-20-record-replay.md)).

**Architecture:** adapter wrappers in `src/da_agent/llm/replay.py`; `LLMClient` prices replayed
responses at $0; `eval/datamate/run.py` gets `--record`, `--replay`, `--replay-miss`.

## Global constraints

- Tests first; offline, no real LLM calls in tests.
- No key, base URL, or header in a cassette line.
- Core has no DataMate code.

## Review focus

- Two eval workers recording at once: lines must not interleave.
- A repeated identical request: outcomes replay in order; one extra call is a miss.
- A cassette line from a failed run (truncated last line): loading must skip it, not crash.
- Replay with a multi-route spec: a miss on route 1 must not silently spend on route 2 in strict
  mode (all strict routes are replay adapters).
- Ledger totals for a replay run report $0 but the original token counts.

## Tasks

### Task 1: keys, cassette, recorder, replayer (`src/da_agent/llm/replay.py`)

Interfaces produced:
- `request_key(provider: str, model: str, request: LLMRequest) -> str`
- `class Cassette` with `load(path) -> Cassette`, `next(provider, model, request)`, `hits`,
  `misses`, `miss_details: list[dict]`
- `class RecordingAdapter(inner: LLMAdapter, path: Path)` and
  `class ReplayAdapter(provider: str, cassette: Cassette, live: LLMAdapter | None = None,
  recorder_path: Path | None = None)`
- `class ReplayMissError(PermanentLLMError)`
- `LLMResponse.replayed: bool = False`

Tests (`tests/unit/test_llm_replay.py`): record then replay gives equal responses and
`replayed=True`; invalid tool call and permanent errors replay as the same type; transient errors
are not recorded; repeats replay in order and the extra call misses; a miss names the first
differing message index and role; live mode calls the inner adapter and records it; a truncated
last line is skipped; no API key text in the file; concurrent writes give whole lines.

### Task 2: $0 for replayed responses (`src/da_agent/llm/client.py`)

Test in `tests/unit/test_llm_client.py`: a replayed response records cost 0 and keeps tokens.

### Task 3: route wrappers and eval wiring

- `parse_route_spec(spec) -> list[tuple[str, str]]` in `factory.py`, used by `build_routes`.
- `record_routes(routes, path) -> list[Route]`, `replay_routes(spec | routes, cassette, ...)` in
  `replay.py`.
- `run.py`: `--record`, `--replay DIR`, `--replay-miss {stop,live}`; `metrics["replay"]`; the CLI
  prints hits and misses. Tests in `tests/unit/test_datamate_eval.py`: a fake-adapter run recorded
  then replayed gives the same answers and $0; strict replay needs no API key.

### Task 4: docs

ADR 0039, learning note 18 (Vietnamese), `docs/eval` or runbook usage lines, progress.

### Final

Full suite, ruff, one real check with the user's go-ahead: record 3 questions on Groq (~$0.01),
replay them, expect 0 misses and identical answers.
