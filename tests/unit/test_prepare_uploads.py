"""prepare_uploads: one dataset per upload, optionally several at once on a local model server."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.ingestion.prepare as prepare_module
from da_agent.llm.client import CostLedger, LLMClient, Route
from eval.ingestion.prepare import make_runner, prepare_uploads


def fake_prepared(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        status="ok", ingest=SimpleNamespace(duration_seconds=0.0), proposal=None, build=None,
        spec=None, dropped_rules=[], deduplicated={},
    )  # fmt: skip


def uploads(tmp_path: Path, names: list[str]) -> dict[str, Path]:
    sources = {}
    for name in names:
        source = tmp_path / "src" / name
        source.mkdir(parents=True)
        (source / "data.csv").write_text("a\n1\n", encoding="utf-8")
        sources[name] = source
    return sources


def test_workers_prepare_uploads_at_the_same_time_and_keep_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2, timeout=5)
    seen: list[str] = []

    def fake_prepare_dataset(name: str, *_args: object, **_kwargs: object) -> SimpleNamespace:
        barrier.wait()
        seen.append(name)
        return fake_prepared(name)

    monkeypatch.setattr(prepare_module, "prepare_dataset", fake_prepare_dataset)
    root = tmp_path / "out"
    runner = make_runner(root, "image")
    llm = LLMClient([Route(SimpleNamespace(provider="unused"), "m")], CostLedger())  # type: ignore[arg-type]

    summary = prepare_uploads(
        uploads(tmp_path, ["shop_b", "shop_a"]), root, runner, llm, CostLedger(),
        description="", max_cost=1.0, max_tokens_per_upload=1000, workers=2,
    )  # fmt: skip

    assert sorted(seen) == ["shop_a", "shop_b"]
    assert [row["shop"] for row in summary] == ["shop_b", "shop_a"]
    assert all(row["status"] == "ok" for row in summary)
