"""The agent manifest: which code, prompts, model routes, and limits produced a run (ADR 0038).

A worker builds one manifest when it starts and an eval run builds one per run. Its version id is a
short hash of the manifest, so the same configuration always gets the same id. Prompts are
fingerprinted by what the model sees (the fixed text and tool specs), not by source files, so
comments and refactors do not change the version; the git sha covers every other code change.
Nothing here reads secrets: routes contribute only their provider, model, and request options.

Code is named by git when the checkout is there: the sha, whether anything changed since, and a
hash of those changes, so two runs on different uncommitted code never share a version. An image
has no checkout, so it names its sha by the DA_AGENT_GIT_SHA build arg.
"""

import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from da_agent.agent import answer, charts, graph, memory, planning, prompts, tools
from da_agent.ingestion import pipeline, propose
from da_agent.llm.client import Route

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_CHARS = 12
FINGERPRINT_CHARS = 16
GIT_TIMEOUT_SECONDS = 5

AGENT_PROMPT_MODULES: tuple[ModuleType, ...] = (
    prompts, planning, tools, charts, graph, memory, answer,
)  # fmt: skip
INGESTION_PROMPT_MODULES: tuple[ModuleType, ...] = (propose, pipeline)

UNKNOWN_CODE: dict[str, Any] = {"sha": "unknown", "dirty": None, "diff": None}


class _NotPlain(Exception):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: Any, chars: int) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:chars]


def fingerprint(value: Any) -> str:
    """A short hash of JSON data, such as metric definitions an eval run reads."""
    return _hash(_plain(value), FINGERPRINT_CHARS)


def _plain(value: Any) -> Any:
    """The value as JSON data, or _NotPlain for anything else, such as a pattern or a function."""
    if isinstance(value, float) and not math.isfinite(value):
        # Postgres jsonb and strict JSON have no Infinity or NaN.
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _plain(item) for key, item in value.items()}
    raise _NotPlain


def prompt_fingerprint(modules: Sequence[ModuleType], samples: Sequence[str] = ()) -> str:
    """Hash the upper-case constants of modules that are plain data, plus sample texts.

    Prompt text, tool specs, tool names, and limits the model reads are all such constants. Text
    written inside functions is covered only by the samples."""
    constants = []
    for module in modules:
        for name in sorted(vars(module)):
            if name.startswith("_") or not name.isupper():
                continue
            try:
                constants.append([module.__name__, name, _plain(vars(module)[name])])
            except _NotPlain:
                continue
    return _hash({"constants": constants, "samples": list(samples)}, FINGERPRINT_CHARS)


def agent_prompt_samples() -> list[str]:
    """Agent text that functions write, rendered with no data."""
    samples = [
        prompts.turns_left_notice(1),
        prompts.turns_left_notice(prompts.WARN_AT_TURNS_LEFT),
        prompts.invalid_tool_call_notice(["tool"]),
        planning.plan_system_prompt([], True),
        planning.plan_system_prompt([], False),
        prompts.build_system_prompt([]),
        prompts.build_system_prompt([], schema_given=True),
    ]
    return [sample for sample in samples if sample is not None]


def _git(repo: Path, *args: str) -> bytes:
    # Fixed arguments, no user input. git is looked up on PATH, as in any local checkout.
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=True,
    )  # fmt: skip
    return done.stdout


def _changes_hash(repo: Path) -> str:
    """A hash of every change since HEAD: the diff of tracked files, and the names and bytes of
    untracked files that git does not ignore."""
    digest = hashlib.sha256(_git(repo, "diff", "HEAD", "--binary"))
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    for name in sorted(name for name in untracked if name):
        digest.update(b"\0" + name + b"\0")
        path = repo / name.decode("utf-8", "surrogateescape")
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:FINGERPRINT_CHARS]


def code_version(env: Mapping[str, str], repo: Path | None = None) -> dict[str, Any]:
    """The checkout's sha, whether it has changes, and a hash of them; without a checkout, as in an
    image, the DA_AGENT_GIT_SHA build arg, which cannot know about changes.

    git comes first, so a stale DA_AGENT_GIT_SHA left in a local .env never names local code."""
    repo = repo or REPO_ROOT
    try:
        sha = _git(repo, "rev-parse", "HEAD").decode().strip()
        dirty = bool(_git(repo, "status", "--porcelain").strip())
        diff = _changes_hash(repo) if dirty else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        baked = env.get("DA_AGENT_GIT_SHA")
        if baked:
            return {"sha": baked, "dirty": None, "diff": None}
        return dict(UNKNOWN_CODE)
    return {"sha": sha, "dirty": dirty, "diff": diff}


def route_summary(routes: Sequence[Route]) -> list[dict[str, Any]]:
    """Provider, model, and request options of each route, in fallback order. No keys or URLs."""
    return [
        {
            "provider": route.adapter.provider,
            "model": route.model,
            "options": dict(getattr(route.adapter, "options", {})),
        }
        for route in routes
    ]


@dataclass(frozen=True)
class AgentManifest:
    code: dict[str, Any]
    prompts: dict[str, str]
    routes: list[dict[str, Any]]
    limits: dict[str, Any]

    def _fields(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "prompts": self.prompts,
            "routes": self.routes,
            "limits": self.limits,
        }

    @property
    def version(self) -> str:
        return _hash(self._fields(), VERSION_CHARS)

    def to_json(self) -> dict[str, Any]:
        return {**self._fields(), "version": self.version}


def build_manifest(
    *,
    routes: Sequence[Route],
    limits: Mapping[str, Any],
    env: Mapping[str, str],
    repo: Path | None = None,
) -> AgentManifest:
    return AgentManifest(
        code=code_version(env, repo),
        prompts={
            "agent": prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples()),
            "ingestion": prompt_fingerprint(INGESTION_PROMPT_MODULES),
        },
        routes=route_summary(routes),
        limits=_plain(dict(limits)),
    )
