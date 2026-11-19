"""The agent manifest: which code, prompts, routes, and limits produced a run (ADR 0038)."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from da_agent.agent import prompts
from da_agent.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from da_agent.llm.client import Route
from da_agent.manifest import (
    AGENT_PROMPT_MODULES,
    agent_prompt_samples,
    build_manifest,
    code_version,
    prompt_fingerprint,
    route_summary,
)


class FakeAdapter:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def complete(self, request, model):  # pragma: no cover - never called
        raise AssertionError


def route(provider: str, model: str) -> Route:
    return Route(FakeAdapter(provider), model)  # type: ignore[arg-type]


SHA = {"DA_AGENT_GIT_SHA": "abc"}


@pytest.fixture(autouse=True)
def outside_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """build_manifest reads git from REPO_ROOT; point it at a folder that is no checkout, as in an
    image, so the build arg decides the sha."""
    monkeypatch.setattr("da_agent.manifest.REPO_ROOT", tmp_path / "no-git")


def test_the_same_configuration_always_gets_the_same_version() -> None:
    a = build_manifest(routes=[route("groq", "m")], limits={"x": 1, "y": 2}, env=SHA)
    b = build_manifest(routes=[route("groq", "m")], limits={"y": 2, "x": 1}, env=SHA)

    assert a.version == b.version
    assert re.fullmatch(r"[0-9a-f]{12}", a.version)


def test_any_field_changes_the_version() -> None:
    base = {"routes": [route("groq", "m")], "limits": {"x": 1}, "env": SHA}
    version = build_manifest(**base).version

    assert build_manifest(**{**base, "limits": {"x": 2}}).version != version
    assert build_manifest(**{**base, "routes": [route("groq", "other")]}).version != version
    assert build_manifest(**{**base, "env": {"DA_AGENT_GIT_SHA": "def"}}).version != version


def test_to_json_holds_every_field_and_the_version() -> None:
    manifest = build_manifest(routes=[route("groq", "m")], limits={"x": 1}, env=SHA)

    saved = manifest.to_json()

    assert saved["version"] == manifest.version
    assert saved["code"] == {"sha": "abc", "dirty": None, "diff": None}
    assert saved["routes"] == [{"provider": "groq", "model": "m", "options": {}}]
    assert saved["limits"] == {"x": 1}


def test_no_secret_reaches_the_manifest() -> None:
    adapter = OpenAICompatibleAdapter(
        provider="groq", base_url="https://x.example/v1", api_key="sk-SECRET-123"
    )
    env = {"GROQ_API_KEY": "sk-SECRET-123", **SHA}

    manifest = build_manifest(routes=[Route(adapter, "m")], limits={}, env=env)

    assert "SECRET" not in json.dumps(manifest.to_json())


def test_route_options_are_recorded_in_order() -> None:
    vllm = OpenAICompatibleAdapter(
        provider="vllm", base_url="http://v.example/v1", api_key="EMPTY", extra_body={"seed": 0}
    )

    summary = route_summary([Route(vllm, "gemma"), route("groq", "m")])

    assert [(r["provider"], r["model"]) for r in summary] == [("vllm", "gemma"), ("groq", "m")]
    assert summary[0]["options"]["extra_body"] == {"seed": 0}
    assert summary[1]["options"] == {}


def test_without_git_the_build_arg_names_the_sha(tmp_path: Path) -> None:
    expected = {"sha": "abc", "dirty": None, "diff": None}
    assert code_version({"DA_AGENT_GIT_SHA": "abc"}, tmp_path) == expected


def test_without_git_or_build_arg_the_sha_is_unknown(tmp_path: Path) -> None:
    assert code_version({}, tmp_path) == {"sha": "unknown", "dirty": None, "diff": None}


def _git(repo: Path, *args: str) -> str:
    command = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    done = subprocess.run([*command, *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("one")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "first")
    return repo


def test_a_clean_checkout_reports_its_sha(checkout: Path) -> None:
    expected = {"sha": _git(checkout, "rev-parse", "HEAD"), "dirty": False, "diff": None}
    assert code_version({}, checkout) == expected


def test_in_a_checkout_git_wins_over_a_stale_build_arg(checkout: Path) -> None:
    assert code_version({"DA_AGENT_GIT_SHA": "stale"}, checkout)["sha"] == _git(
        checkout, "rev-parse", "HEAD"
    )


def test_different_uncommitted_changes_get_different_diffs(checkout: Path) -> None:
    (checkout / "f.txt").write_text("two")
    first = code_version({}, checkout)
    (checkout / "f.txt").write_text("three")
    second = code_version({}, checkout)

    assert first["dirty"] is True and second["dirty"] is True
    assert re.fullmatch(r"[0-9a-f]{16}", first["diff"])
    assert first["diff"] != second["diff"]


def test_an_untracked_file_counts_as_a_change(checkout: Path) -> None:
    (checkout / "new.py").write_text("A = 1")
    first = code_version({}, checkout)
    (checkout / "new.py").write_text("A = 2")
    second = code_version({}, checkout)

    assert first["dirty"] is True
    assert first["diff"] != second["diff"]


def test_a_non_finite_limit_still_makes_strict_json() -> None:
    manifest = build_manifest(
        routes=[route("groq", "m")], limits={"daily_usd": float("inf")}, env=SHA
    )

    json.dumps(manifest.to_json(), allow_nan=False)
    assert manifest.limits == {"daily_usd": "inf"}


def test_a_prompt_constant_changes_its_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    before = prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples())

    monkeypatch.setattr(prompts, "NUDGE", prompts.NUDGE + " ")

    assert prompt_fingerprint(AGENT_PROMPT_MODULES, agent_prompt_samples()) != before
    assert re.fullmatch(r"[0-9a-f]{16}", before)


def test_lowercase_names_and_unplain_values_do_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    before = prompt_fingerprint([prompts])

    monkeypatch.setattr(prompts, "helper_text", "x", raising=False)
    monkeypatch.setattr(prompts, "SOME_PATTERN", re.compile("x"), raising=False)

    assert prompt_fingerprint([prompts]) == before


def test_the_manifest_has_one_fingerprint_per_prompt_family() -> None:
    manifest = build_manifest(routes=[route("groq", "m")], limits={}, env=SHA)

    assert set(manifest.prompts) == {"agent", "ingestion"}
    assert manifest.prompts["agent"] != manifest.prompts["ingestion"]
