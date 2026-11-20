"""Record model outcomes and replay them, so code after the model can be rerun at no cost.

See docs/design/2026-11-20-record-replay.md and ADR 0039. A cassette is a JSONL file with one line
per model outcome, keyed by a hash of the provider, model, and whole request. It holds what the
model saw and said, so it lives with eval reports and is never committed. It never holds keys.
"""

import hashlib
import json
import threading
from collections import defaultdict, deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from da_agent.llm.client import ModelPrice, Route
from da_agent.llm.types import (
    InvalidToolCallError,
    LLMAdapter,
    LLMRequest,
    LLMResponse,
    Message,
    PermanentLLMError,
)

# Outcomes that depend on the request, so a replay reproduces them. Rate limits, timeouts, and
# server errors are network events: they are not recorded, and on replay the call succeeds.
_RECORDED_ERRORS: dict[str, type[PermanentLLMError] | type[InvalidToolCallError]] = {
    "invalid_tool_call": InvalidToolCallError,
    "permanent": PermanentLLMError,
}

# One lock for every cassette write in the process, so eval workers never interleave lines.
_WRITE_LOCK = threading.Lock()


class ReplayMissError(PermanentLLMError):
    """The cassette has no outcome for this request. The agent ends the question with it."""


def _hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_key(provider: str, model: str, request: LLMRequest) -> str:
    return _hash({"provider": provider, "model": model, "request": request.model_dump(mode="json")})


def _message_hashes(messages: Sequence[Message]) -> list[list[str]]:
    return [[message.role, _hash(message.model_dump(mode="json"))[:16]] for message in messages]


def _append(path: Path, line: dict[str, Any]) -> None:
    text = json.dumps(line, ensure_ascii=False) + "\n"
    with _WRITE_LOCK, path.open("a", encoding="utf-8") as file:
        file.write(text)


def _record(path: Path, provider: str, model: str, request: LLMRequest, outcome: Any) -> None:
    line: dict[str, Any] = {
        "key": request_key(provider, model, request),
        "provider": provider,
        "model": model,
        "messages": _message_hashes(request.messages),
    }
    if isinstance(outcome, LLMResponse):
        line["outcome"] = "response"
        line["response"] = outcome.model_dump(
            mode="json", exclude={"provider", "model", "replayed"}
        )
    else:
        kind = "invalid_tool_call" if isinstance(outcome, InvalidToolCallError) else "permanent"
        line["outcome"] = kind
        line["error"] = str(outcome)
    _append(path, line)


class RecordingAdapter:
    """Calls the wrapped adapter and appends each outcome that depends on the request."""

    def __init__(self, inner: LLMAdapter, path: Path) -> None:
        self.provider = inner.provider
        self._inner = inner
        self._path = path

    @property
    def options(self) -> dict[str, Any]:
        return dict(getattr(self._inner, "options", {}))

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        try:
            response = self._inner.complete(request, model)
        except (InvalidToolCallError, PermanentLLMError) as error:
            _record(self._path, self.provider, model, request, error)
            raise
        _record(self._path, self.provider, model, request, response)
        return response


class Cassette:
    """The recorded outcomes of one run, served in recorded order per key. Thread-safe."""

    def __init__(self, lines: list[dict[str, Any]], source: str = "") -> None:
        self.source = source
        self._lock = threading.Lock()
        self._queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._lines = lines
        for line in lines:
            self._queues[line["key"]].append(line)
        self.hits = 0
        self.misses = 0
        self.miss_details: list[dict[str, Any]] = []

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        lines = []
        for text in path.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(text)
            except json.JSONDecodeError:
                continue  # A run killed mid-write leaves a partial last line.
            if isinstance(line, dict) and "key" in line and "outcome" in line:
                lines.append(line)
        return cls(lines, source=str(path))

    def take(self, provider: str, model: str, request: LLMRequest) -> dict[str, Any] | None:
        """The next recorded outcome for this request, or None after recording the miss."""
        key = request_key(provider, model, request)
        with self._lock:
            queue = self._queues.get(key)
            if queue:
                self.hits += 1
                return queue.popleft()
            self.misses += 1
            detail = self._divergence(provider, model, request)
            self.miss_details.append(detail)
            return None

    def last_miss(self) -> dict[str, Any]:
        with self._lock:
            return self.miss_details[-1]

    def _divergence(self, provider: str, model: str, request: LLMRequest) -> dict[str, Any]:
        """Where this request first differs from the closest recorded one of the same route."""
        hashes = _message_hashes(request.messages)
        best: list[list[str]] | None = None
        best_common = -1
        for line in self._lines:
            if (line["provider"], line["model"]) != (provider, model):
                continue
            recorded = line["messages"]
            common = 0
            for mine, theirs in zip(hashes, recorded, strict=False):
                if mine != theirs:
                    break
                common += 1
            if common > best_common:
                best, best_common = recorded, common
        detail: dict[str, Any] = {"provider": provider, "model": model}
        if best is None:
            detail["first_difference"] = None
            detail["reason"] = f"no recorded request for {provider}/{model}"
        elif best_common == len(hashes) == len(best):
            detail["first_difference"] = None
            detail["reason"] = (
                "the messages match a recorded request, but tools or options differ, "
            )
            detail["reason"] += "or it was called more times than recorded"
        else:
            detail["first_difference"] = best_common
            role = hashes[best_common][0] if best_common < len(hashes) else "end"
            detail["reason"] = f"first differs at message {best_common} ({role})"
        return detail


def _replayed(line: dict[str, Any]) -> LLMResponse:
    if line["outcome"] == "response":
        return LLMResponse.model_validate({**line["response"], "replayed": True})
    raise _RECORDED_ERRORS.get(line["outcome"], PermanentLLMError)(line.get("error", ""))


class ReplayAdapter:
    """Answers from a cassette. On a miss, raises ReplayMissError, or with a live adapter calls
    the model and records the outcome to recorder_path."""

    def __init__(
        self,
        provider: str,
        cassette: Cassette,
        live: LLMAdapter | None = None,
        recorder_path: Path | None = None,
    ) -> None:
        self.provider = provider
        self._cassette = cassette
        self._live = live
        self._recorder_path = recorder_path

    @property
    def options(self) -> dict[str, Any]:
        options = dict(getattr(self._live, "options", {})) if self._live is not None else {}
        options["replay"] = "live" if self._live is not None else "stop"
        return options

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        line = self._cassette.take(self.provider, model, request)
        if line is not None:
            if self._recorder_path is not None:
                _append(self._recorder_path, line)
            return _replayed(line)
        if self._live is None:
            reason = self._cassette.last_miss()["reason"]
            raise ReplayMissError(f"replay miss for {self.provider}/{model}: {reason}")
        if self._recorder_path is None:
            return self._live.complete(request, model)
        return RecordingAdapter(self._live, self._recorder_path).complete(request, model)


def record_routes(routes: Sequence[Route], path: Path) -> list[Route]:
    """The same routes, each recording to path."""
    return [
        Route(RecordingAdapter(route.adapter, path), route.model, route.price) for route in routes
    ]


def replay_routes(
    routes: Sequence[Route] | Sequence[tuple[str, str]],
    cassette: Cassette,
    recorder_path: Path | None = None,
) -> list[Route]:
    """Routes that answer from the cassette. Given (provider, model) pairs, a miss stops the
    question and nothing needs a key. Given real routes, a miss calls that route's model."""
    replay = []
    for route in routes:
        if isinstance(route, Route):
            adapter = ReplayAdapter(route.adapter.provider, cassette, route.adapter, recorder_path)
            replay.append(Route(adapter, route.model, route.price))
        else:
            provider, model = route
            adapter = ReplayAdapter(provider, cassette, recorder_path=recorder_path)
            replay.append(Route(adapter, model, ModelPrice()))
    return replay
