"""Eval splits: shops split into dev and holdout, and question sets drawn from dev only."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from eval.scorer.scoring import load_answer_types
from eval.splits.make_splits import (
    ALREADY_INSPECTED,
    Question,
    Shop,
    assign_shops,
    build_question_sets,
    load_question_set,
    sample_questions,
)

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_SPLITS = ROOT / "eval" / "splits" / "splits.json"


def make_shops() -> list[Shop]:
    limited = [Shop(f"lim_{i}", "limited", "csv") for i in range(5)]
    csv = [Shop(f"csv_{i}", "full", "csv") for i in range(5)]
    xlsx = [Shop(f"xls_{i}", "full", "xlsx") for i in range(20)]
    return limited + csv + xlsx


def make_questions(shops: list[str], per_shop: int = 40) -> list[Question]:
    questions = []
    for shop in shops:
        for index in range(per_shop):
            kind = "table" if index == 0 else "percent" if index % 4 == 0 else "number"
            questions.append(Question(f"{shop}_q{index}", shop, kind))
    return questions


def test_assign_shops_produces_disjoint_groups_of_the_right_size() -> None:
    dev, holdout = assign_shops(make_shops(), holdout_size=6, seed=1)

    assert len(holdout) == 6
    assert len(dev) == 24
    assert not set(dev) & set(holdout)


def test_assign_shops_is_deterministic() -> None:
    assert assign_shops(make_shops(), 6, seed=1) == assign_shops(make_shops(), 6, seed=1)


def test_assign_shops_keeps_inspected_shops_in_dev() -> None:
    protected = {"xls_0", "lim_0", "csv_0"}

    for seed in range(20):
        dev, holdout = assign_shops(make_shops(), 6, seed=seed, protected=protected)

        assert protected <= set(dev)
        assert not protected & set(holdout)


def test_assign_shops_balances_data_completeness_and_format() -> None:
    _, holdout = assign_shops(make_shops(), 6, seed=1)
    by_id = {shop.id: shop for shop in make_shops()}
    strata = Counter((by_id[id_].completeness, by_id[id_].file_format) for id_ in holdout)

    assert strata == {("limited", "csv"): 1, ("full", "csv"): 1, ("full", "xlsx"): 4}


def test_assign_shops_rejects_an_impossible_holdout() -> None:
    with pytest.raises(ValueError, match="holdout"):
        assign_shops(make_shops()[:3], 6, seed=1)


def test_sample_has_the_exact_size_without_duplicates() -> None:
    questions = make_questions(["a", "b", "c"])

    sample = sample_questions(questions, 30, seed=1)

    assert len(sample) == 30
    assert len(set(sample)) == 30


def test_sample_is_deterministic() -> None:
    questions = make_questions(["a", "b", "c"])

    assert sample_questions(questions, 30, seed=1) == sample_questions(questions, 30, seed=1)


def test_sample_keeps_rare_types_and_follows_type_shares() -> None:
    questions = make_questions(["a", "b", "c"])
    kinds = {question.id: question.kind for question in questions}

    counts = Counter(kinds[id_] for id_ in sample_questions(questions, 30, seed=1))

    assert counts["table"] >= 1
    assert counts["number"] > counts["percent"] > 0


def test_sample_spreads_each_type_across_shops() -> None:
    questions = make_questions(["a", "b", "c"])
    shops = {question.id: question.shop for question in questions}

    counts = Counter(shops[id_] for id_ in sample_questions(questions, 30, seed=1))

    assert max(counts.values()) - min(counts.values()) <= 3
    assert set(counts) == {"a", "b", "c"}


def test_sample_rejects_a_size_larger_than_the_pool() -> None:
    with pytest.raises(ValueError, match="pool"):
        sample_questions(make_questions(["a"], per_shop=5), 6, seed=1)


def test_question_sets_never_touch_holdout_shops() -> None:
    questions = make_questions(["dev_a", "dev_b", "holdout_a"])

    sets = build_question_sets(
        questions,
        dev_shops=["dev_a", "dev_b"],
        bootstrap_shop="dev_a",
        seed=1,
        smoke=10,
        regression=30,
    )

    assert not [id_ for ids in sets.values() for id_ in ids if id_.startswith("holdout_a")]


def test_question_sets_sizes_nesting_and_bootstrap_shop() -> None:
    questions = make_questions(["dev_a", "dev_b"])

    sets = build_question_sets(
        questions,
        dev_shops=["dev_a", "dev_b"],
        bootstrap_shop="dev_a",
        seed=1,
        smoke=10,
        regression=30,
    )

    assert len(sets["smoke"]) == 10
    assert len(sets["regression"]) == 30
    assert set(sets["smoke"]) <= set(sets["regression"])
    assert len(sets["bootstrap"]) == 10
    assert all(id_.startswith("dev_a") for id_ in sets["bootstrap"])


def test_load_answer_types_returns_only_types(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    row = {"id": "q1", "type": "string", "answer": "Bí mật", "alias": ["SP0009"]}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert load_answer_types(path) == {"q1": "string"}


def test_load_question_set_rejects_unknown_names(tmp_path: Path) -> None:
    path = tmp_path / "splits.json"
    path.write_text(json.dumps({"question_sets": {"smoke": ["q1"]}}), encoding="utf-8")

    assert load_question_set(path, "smoke") == {"q1"}
    with pytest.raises(ValueError, match="holdout"):
        load_question_set(path, "holdout")


def test_scorer_cli_can_score_one_question_set(tmp_path: Path) -> None:
    answers = tmp_path / "answers.jsonl"
    rows = [
        {"id": "q1", "type": "number", "answer": 1, "alias": []},
        {"id": "q2", "type": "number", "answer": 2, "alias": []},
    ]
    answers.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    submission = tmp_path / "submission.jsonl"
    submission.write_text('{"id": "q1", "type": "number", "answer": 1}\n', encoding="utf-8")
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"question_sets": {"smoke": ["q1"]}}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "eval.scorer.cli",
            "--answers",
            str(answers),
            "--submission",
            str(submission),
            "--splits",
            str(splits),
            "--set",
            "smoke",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert (summary["total"], summary["score"]) == (1, 1)


def test_committed_splits_keep_their_guarantees() -> None:
    splits = json.loads(COMMITTED_SPLITS.read_text(encoding="utf-8"))
    dev, holdout = set(splits["dev_shops"]), set(splits["holdout_shops"])
    sets = splits["question_sets"]

    assert (len(dev), len(holdout)) == (24, 6)
    assert not dev & holdout
    assert dev >= ALREADY_INSPECTED
    assert (len(sets["smoke"]), len(sets["regression"])) == (30, 150)
    assert set(sets["smoke"]) <= set(sets["regression"])
    assert len(sets["bootstrap"]) == 30
    assert "holdout" not in sets
