"""pass^k over repeated eval runs (docs/design/2026-11-20-pass-k.md)."""

import json
from pathlib import Path

import pytest

from eval.datamate.passk import run_passk, summarize


def test_summary_counts_questions_right_every_time_and_at_least_once() -> None:
    runs = [
        {"a": "correct", "b": "correct", "c": "wrong", "d": "abstain"},
        {"a": "correct", "b": "wrong", "c": "wrong", "d": "abstain"},
        {"a": "correct", "b": "correct", "c": "wrong", "d": "abstain"},
    ]

    summary = summarize(runs)

    assert summary["k"] == 3
    assert summary["questions"] == 4
    assert summary["pass_hat_k"] == 0.25
    assert summary["pass_at_k"] == 0.5
    assert summary["mean_accuracy"] == pytest.approx(5 / 12)
    assert summary["flaky"] == {"b": ["correct", "wrong", "correct"]}
    assert summary["always_wrong"] == ["c"]
    assert summary["always_abstain"] == ["d"]


def test_summary_reports_the_spread_of_the_score() -> None:
    runs = [
        {"a": "correct", "b": "correct"},  # +2
        {"a": "correct", "b": "wrong"},  # 0
        {"a": "correct", "b": "abstain"},  # +1
    ]

    summary = summarize(runs)

    assert [run["score"] for run in summary["runs"]] == [2, 0, 1]
    assert summary["runs"][1] == {"score": 0, "correct": 1, "wrong": 1, "abstained": 0}
    assert (summary["score_mean"], summary["score_min"], summary["score_max"]) == (1, 0, 2)
    assert summary["score_std"] == pytest.approx(1.0)


def test_a_question_missing_from_a_run_counts_as_abstained() -> None:
    summary = summarize([{"a": "correct"}, {}])

    assert summary["flaky"] == {"a": ["correct", "abstain"]}


def test_k_must_be_at_least_two() -> None:
    with pytest.raises(ValueError, match="at least two"):
        summarize([{"a": "correct"}])


def test_run_passk_runs_k_times_with_seeds_and_writes_the_report(tmp_path: Path) -> None:
    calls: list[tuple[Path, int]] = []

    def run_one(output_dir: Path, seed: int) -> None:
        calls.append((output_dir, seed))
        output_dir.mkdir(parents=True, exist_ok=True)

    def score_one(output_dir: Path) -> dict[str, str]:
        return {"a": "correct", "b": "wrong" if output_dir.name == "run-2" else "correct"}

    summary = run_passk(tmp_path, k=3, seed_base=10, run_one=run_one, score_one=score_one)

    assert calls == [(tmp_path / f"run-{i}", 9 + i) for i in (1, 2, 3)]
    assert summary["seeds"] == [10, 11, 12]
    assert summary["flaky"] == {"b": ["correct", "wrong", "correct"]}
    assert json.loads((tmp_path / "passk.json").read_text(encoding="utf-8")) == summary


def test_the_holdout_set_is_refused(tmp_path: Path) -> None:
    from eval.datamate.passk import main

    with pytest.raises(SystemExit):
        main(["--data-dir", str(tmp_path), "--set", "holdout", "--output-dir", str(tmp_path)])
    assert not (tmp_path / "run-1").exists()
