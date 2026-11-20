"""Record model outcomes and replay them, so code after the model can be rerun at no cost.

See docs/design/2026-11-20-record-replay.md and ADR 0039. A cassette is a JSONL file with one line
per model outcome, keyed by a hash of the provider, model, and whole request. It holds what the
model saw and said, so it lives with eval reports and is never committed. It never holds keys.

A caller that runs many questions wraps each one in replay_scope(question_id). Lines then name the
question and the attempt, so two questions with the same request keep their own outcomes, and a
question retried after a failure replays its latest attempt first.
"""

import hashlib
import json
import logging
import re
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
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

logger = logging.getLogger(__name__)

# Outcomes that depend on the request, so a replay reproduces them. Rate limits, timeouts, and
# server errors are network events: they are not recorded, and on replay the call succeeds.
_RECORDED_ERRORS: dict[str, type[PermanentLLMError] | type[InvalidToolCallError]] = {
    "invalid_tool_call": InvalidToolCallError,
    "permanent": PermanentLLMError,
}

# One lock for every cassette write in the process, so eval workers never interleave lines.
_WRITE_LOCK = threading.Lock()

# The question and attempt being run in this thread: (run id, attempt id).
_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar("replay_scope", default=None)

# Provider error text can echo a masked key or a dashboard URL. Neither is kept.
_SECRETS = re.compile(
    r"https?://\S+|\b(?:sk|gsk|pk|rk)[-_][\w\-.*…]+|\bAIza[\w\-]+|[A-Za-z0-9_\-]{32,}"
)


class ReplayMissError(PermanentLLMError):
    """The cassette has no outcome for this request. The agent ends the question with it."""


@contextmanager
def replay_scope(run_id: str) -> Iterator[None]:
    """Mark the model calls inside as one attempt of one question."""
    token = _SCOPE.set((run_id, uuid.uuid4().hex))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _current_run() -> str | None:
    scope = _SCOPE.get()
    return scope[0] if scope else None


def _hash(value: Any) -> str:
    # ASCII escapes, so a lone surrogate from a model's "\ud83d" never fails to encode.
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def request_key(provider: str, model: str, request: LLMRequest) -> str:
    return _hash({"provider": provider, "model": model, "request": request.model_dump(mode="json")})


def _message_hashes(messages: Sequence[Message]) -> list[list[str]]:
    return [[message.role, _hash(message.model_dump(mode="json"))[:16]] for message in messages]


def _scrub(text: str) -> str:
    return _SECRETS.sub("<redacted>", text)


def _append(path: Path, line: dict[str, Any]) -> None:
    """Write one line. A cassette is a record: a failed write is logged, never fails the call."""
    scope = _SCOPE.get()
    line = {**line, "run": scope[0] if scope else None, "attempt": scope[1] if scope else None}
    try:
        text = json.dumps(line, ensure_ascii=True) + "\n"
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as file:
            file.write(text)
    except (OSError, ValueError) as error:
        logger.warning("cassette write to %s failed: %s", path, error)


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
        line["error"] = _scrub(str(outcome))
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
    """The recorded outcomes of a run, served per question and key. Thread-safe.

    For one question and key, outcomes of a later attempt come first, each attempt in recorded
    order. Earlier attempts stay behind them, for turns that only they recorded (a run resumed
    from a checkpoint continues where the earlier attempt stopped).
    """

    def __init__(self, lines: list[dict[str, Any]], source: str = "") -> None:
        self.source = source
        self._lock = threading.Lock()
        self._lines = lines
        rank: dict[Any, int] = {}
        for line in lines:
            rank.setdefault(line.get("attempt"), len(rank))
        grouped: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
        for line in lines:
            grouped[(line.get("run"), line["key"])].append(line)
        self._queues: dict[tuple[Any, str], deque[dict[str, Any]]] = {
            slot: deque(sorted(group, key=lambda line: -rank[line.get("attempt")]))
            for slot, group in grouped.items()
        }
        self._missed: set[tuple[Any, str]] = set()
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

    def _slot(self, provider: str, model: str, request: LLMRequest) -> tuple[Any, str]:
        return (_current_run(), request_key(provider, model, request))

    def has(self, provider: str, model: str, request: LLMRequest) -> bool:
        slot = self._slot(provider, model, request)
        with self._lock:
            return bool(self._queues.get(slot))

    def take(self, provider: str, model: str, request: LLMRequest) -> dict[str, Any] | None:
        """The next recorded outcome for this request in this question, counted as a hit."""
        slot = self._slot(provider, model, request)
        with self._lock:
            queue = self._queues.get(slot)
            if not queue:
                return None
            self.hits += 1
            return queue.popleft()

    def miss(self, provider: str, model: str, request: LLMRequest) -> dict[str, Any]:
        """Count a request no route could answer, once per question and key (retries of a live
        call repeat it), and say where it differs from the closest recording."""
        slot = self._slot(provider, model, request)
        with self._lock:
            detail = self._divergence(provider, model, request)
            if slot not in self._missed:
                self._missed.add(slot)
                self.misses += 1
                self.miss_details.append(detail)
            return detail

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
        detail: dict[str, Any] = {"run": _current_run(), "provider": provider, "model": model}
        if best is None:
            detail["first_difference"] = None
            detail["reason"] = f"no recorded request for {provider}/{model}"
        elif best_common == len(hashes) == len(best):
            detail["first_difference"] = None
            detail["reason"] = (
                "the messages match a recorded request, but tools or options differ, "
                "or it was called more times than recorded"
            )
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
    the model and records the outcome to recorder_path.

    later_routes are the (provider, model) pairs after this one. When one of them has the request
    recorded, this route steps aside with ReplayMissError, so the client falls back to it as in
    the recorded run, instead of counting a miss or calling a model.
    """

    def __init__(
        self,
        provider: str,
        cassette: Cassette,
        live: LLMAdapter | None = None,
        recorder_path: Path | None = None,
        later_routes: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.provider = provider
        self._cassette = cassette
        self._live = live
        self._recorder_path = recorder_path
        self._later_routes = list(later_routes)

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
        if any(self._cassette.has(p, m, request) for p, m in self._later_routes):
            raise ReplayMissError(f"replay: {self.provider}/{model} failed when recorded")
        detail = self._cassette.miss(self.provider, model, request)
        if self._live is None:
            raise ReplayMissError(f"replay miss for {self.provider}/{model}: {detail['reason']}")
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
    pairs = [
        (route.adapter.provider, route.model) if isinstance(route, Route) else route
        for route in routes
    ]
    replay = []
    for index, route in enumerate(routes):
        later = pairs[index + 1 :]
        if isinstance(route, Route):
            adapter = ReplayAdapter(
                route.adapter.provider, cassette, route.adapter, recorder_path, later
            )
            replay.append(Route(adapter, route.model, route.price))
        else:
            provider, model = route
            adapter = ReplayAdapter(provider, cassette, None, recorder_path, later)
            replay.append(Route(adapter, model, ModelPrice()))
    return replay
