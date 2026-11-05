"""Scorer behaviour, taken from the scoring rules in section 9 of the DataMate rulebook."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from eval.scorer.scoring import Outcome, load_jsonl, score_item, score_submission

ROOT = Path(__file__).resolve().parents[2]


def truth(kind: str, answer: Any, alias: list | None = None, id_: str = "q1") -> dict:
    return {"id": id_, "type": kind, "answer": answer, "alias": alias or []}


def submit(kind: str, answer: Any, id_: str = "q1") -> dict:
    return {"id": id_, "type": kind, "answer": answer}


def outcome(kind: str, expected: Any, given: Any, alias: list | None = None) -> Outcome:
    return score_item(truth(kind, expected, alias), submit(kind, given)).outcome


@pytest.mark.parametrize(
    ("result", "points"),
    [(Outcome.CORRECT, 1), (Outcome.WRONG, -1), (Outcome.ABSTAIN, 0)],
)
def test_points_follow_the_outcome(result: Outcome, points: int) -> None:
    given = {
        Outcome.CORRECT: submit("number", 5),
        Outcome.WRONG: submit("number", 6),
        Outcome.ABSTAIN: {"id": "q1", "type": "abstain"},
    }[result]

    scored = score_item(truth("number", 5), given)

    assert scored.outcome is result
    assert scored.points == points


def test_missing_submission_is_an_abstention() -> None:
    assert score_item(truth("number", 5), None).outcome is Outcome.ABSTAIN


def test_submitted_type_must_match() -> None:
    assert score_item(truth("percent", 12.4), submit("number", 12.4)).outcome is Outcome.WRONG


@pytest.mark.parametrize(
    "given",
    [4820000, 4820000.0, 4820000.0000000001],
)
def test_number_accepts_exact_values_and_float_noise(given: float) -> None:
    assert outcome("number", 4820000, given) is Outcome.CORRECT


@pytest.mark.parametrize(
    "given",
    ["4820000", "4,82 triệu", "4.820.000", "4820K", True, None, [4820000]],
)
def test_number_rejects_anything_but_a_json_number(given: Any) -> None:
    scored = score_item(truth("number", 4820000), submit("number", given))

    assert scored.outcome is Outcome.WRONG
    assert scored.reason == "format"


@pytest.mark.parametrize("given", [float("nan"), float("inf"), float("-inf")])
def test_number_rejects_non_finite_values(given: float) -> None:
    assert outcome("number", 5, given) is Outcome.WRONG


def test_number_survives_values_too_large_for_a_float() -> None:
    assert outcome("number", 5.5, 10**400) is Outcome.WRONG


def test_money_must_match_to_the_dong_even_when_large() -> None:
    assert outcome("number", 3_000_000_000, 3_000_000_001) is Outcome.WRONG


def test_percent_uses_percent_points_and_exact_value() -> None:
    assert outcome("percent", 12.4, 12.4) is Outcome.CORRECT
    assert outcome("percent", 12.4, 0.124) is Outcome.WRONG
    assert outcome("percent", 2.08, 2.081) is Outcome.WRONG
    assert outcome("percent", 12.4, "12,4%") is Outcome.WRONG


@pytest.mark.parametrize(
    ("expected", "given", "result"),
    [
        ("2026-08-15", "2026-08-15", Outcome.CORRECT),
        ("2025-09", "2025-09", Outcome.CORRECT),
        ("2026-08-15", "2026-08-16", Outcome.WRONG),
        ("2026-08-15", "15/08/2026", Outcome.WRONG),
        ("2026-08-15", "2026-8-15", Outcome.WRONG),
        ("2026-08-15", "20260815", Outcome.WRONG),
        ("2025-09", "tháng 9/2025", Outcome.WRONG),
        ("2025-09", "2025-09-01", Outcome.WRONG),
        ("2026-02-28", "2026-02-31", Outcome.WRONG),
    ],
)
def test_date_formats(expected: str, given: str, result: Outcome) -> None:
    assert outcome("date", expected, given) is result


def test_boolean_accepts_only_json_booleans() -> None:
    assert outcome("boolean", True, True) is Outcome.CORRECT
    assert outcome("boolean", True, False) is Outcome.WRONG
    assert outcome("boolean", True, 1) is Outcome.WRONG
    assert outcome("boolean", True, "có") is Outcome.WRONG


@pytest.mark.parametrize(
    "given",
    [
        "Sting dâu 330ml",
        "sting dâu 330ML",
        "Sting  dâu\u00a0330ml",
        "Sting dâu, 330ml.",
        "Sting da\u0302u 330ml",  # the same text in decomposed Unicode form
    ],
)
def test_string_ignores_case_whitespace_punctuation_and_unicode_form(given: str) -> None:
    assert outcome("string", "Sting dâu 330ml", given) is Outcome.CORRECT


@pytest.mark.parametrize("given", ["Sting dầu 330ml", "Sting dau 330ml", ["Sting dâu 330ml"], 330])
def test_string_requires_correct_diacritics_and_a_plain_string(given: Any) -> None:
    assert outcome("string", "Sting dâu 330ml", given) is Outcome.WRONG


def test_string_accepts_aliases() -> None:
    alias = ["SP0001", "Sting dau lon"]

    assert outcome("string", "Sting dâu 330ml", "sp0001", alias) is Outcome.CORRECT
    assert outcome("string", "Sting dâu 330ml", "Sting dau lon", alias) is Outcome.CORRECT
    assert outcome("string", "Sting dâu 330ml", "SP0002", alias) is Outcome.WRONG


def test_list_needs_the_same_items_in_the_same_order() -> None:
    assert outcome("list", [3, 10], [3, 10]) is Outcome.CORRECT
    assert outcome("list", [3, 10], [10, 3]) is Outcome.WRONG
    assert outcome("list", [3, 10], [3]) is Outcome.WRONG
    assert outcome("list", [3, 10], [3, 10, 10]) is Outcome.WRONG


def test_single_item_list_must_still_be_a_list() -> None:
    assert outcome("list", ["Cà phê sữa"], ["Cà phê sữa"]) is Outcome.CORRECT
    assert outcome("list", ["Cà phê sữa"], "Cà phê sữa") is Outcome.WRONG


def test_list_items_use_their_own_aliases() -> None:
    expected = ["Cà phê sữa", "Bạc xỉu"]
    alias = [["SP0003"], ["SP0017"]]

    assert outcome("list", expected, ["SP0003", "bạc xỉu"], alias) is Outcome.CORRECT
    assert outcome("list", expected, ["SP0017", "Bạc xỉu"], alias) is Outcome.WRONG


def test_list_numbers_must_be_json_numbers() -> None:
    assert outcome("list", [3, 10], ["3", "10"]) is Outcome.WRONG


TABLE = {
    "columns": ["khach_hang", "so_tien_no"],
    "rows": [["Chị Lan", 3200000], ["Anh Minh", 150000]],
}
TABLE_ALIAS = [["KH0001"], ["KH0002"]]


def test_table_ignores_row_order_and_accepts_row_aliases() -> None:
    given = {
        "columns": ["khach_hang", "so_tien_no"],
        "rows": [["KH0002", 150000], ["chị lan", 3200000]],
    }

    assert outcome("table", TABLE, given, TABLE_ALIAS) is Outcome.CORRECT


def test_table_matches_columns_by_name_first() -> None:
    given = {
        "columns": ["so_tien_no", "khach_hang"],
        "rows": [[3200000, "Chị Lan"], [150000, "Anh Minh"]],
    }

    assert outcome("table", TABLE, given) is Outcome.CORRECT


def test_table_falls_back_to_position_when_names_differ() -> None:
    given = {"columns": ["ten", "no"], "rows": [["Chị Lan", 3200000], ["Anh Minh", 150000]]}
    swapped = {"columns": ["no", "ten"], "rows": [[3200000, "Chị Lan"], [150000, "Anh Minh"]]}

    assert outcome("table", TABLE, given) is Outcome.CORRECT
    assert outcome("table", TABLE, swapped) is Outcome.WRONG


@pytest.mark.parametrize(
    "rows",
    [
        [["Chị Lan", 3200000]],
        [["Chị Lan", 3200000], ["Anh Minh", 150000], ["Chú Ba", 1]],
        [["Chị Lan", 3200000], ["Anh Minh", 150001]],
        [["Chị Lan", 3200000], ["Chị Lan", 3200000]],
    ],
)
def test_table_needs_every_row_exactly_once(rows: list) -> None:
    given = {"columns": ["khach_hang", "so_tien_no"], "rows": rows}

    assert outcome("table", TABLE, given) is Outcome.WRONG


@pytest.mark.parametrize(
    "given",
    [
        "Chị Lan 3200000",
        [["Chị Lan", 3200000]],
        {"rows": TABLE["rows"]},
        {"columns": TABLE["columns"]},
        {"columns": "khach_hang", "rows": TABLE["rows"]},
        {"columns": [1, 2], "rows": TABLE["rows"]},
        {"columns": TABLE["columns"], "rows": [["Chị Lan"], ["Anh Minh"]]},
        {"columns": TABLE["columns"], "rows": ["Chị Lan", "Anh Minh"]},
        {"columns": TABLE["columns"], "rows": [["Chị Lan", "3.200.000đ"], ["Anh Minh", 150000]]},
    ],
)
def test_table_format_errors(given: Any) -> None:
    scored = score_item(truth("table", TABLE), submit("table", given))

    assert scored.outcome is Outcome.WRONG
    assert scored.reason == "format"


@pytest.mark.parametrize("extra", [{}, {"reason": "No purchase price"}, {"reason": "  "}])
def test_unanswerable_reason_is_optional(extra: dict) -> None:
    given = {"id": "q1", "type": "unanswerable", **extra}

    assert score_item(truth("unanswerable", None), given).outcome is Outcome.CORRECT


@pytest.mark.parametrize("reason", [None, 5, ["Missing"], {"why": "Missing"}])
@pytest.mark.parametrize("kind", ["unanswerable", "number"])
def test_non_string_reason_makes_the_line_unreadable_and_counts_as_abstention(
    kind: str, reason: object
) -> None:
    given = {"id": "q1", "type": "unanswerable", "reason": reason}

    assert score_item(truth(kind, None if kind == "unanswerable" else 5), given).outcome is (
        Outcome.ABSTAIN
    )


def test_unanswerable_outcomes_when_either_side_differs() -> None:
    no_answer = {"id": "q1", "type": "unanswerable", "reason": "Missing"}

    assert score_item(truth("number", 5), no_answer).outcome is Outcome.WRONG
    assert score_item(truth("unanswerable", None), submit("number", 5)).outcome is Outcome.WRONG
    assert score_item(truth("unanswerable", None), None).outcome is Outcome.ABSTAIN


def test_scored_item_never_reveals_the_answer() -> None:
    scored = score_item(truth("string", "Bí mật", ["SP0009"]), submit("string", "Khác"))

    assert "Bí mật" not in repr(scored)
    assert "SP0009" not in repr(scored)


def test_report_totals_and_breakdown_by_type() -> None:
    truths = [
        truth("number", 1, id_="a"),
        truth("number", 2, id_="b"),
        truth("percent", 3.5, id_="c"),
        truth("boolean", True, id_="d"),
    ]
    submissions = [
        submit("number", 1, id_="a"),
        submit("number", 9, id_="b"),
        {"id": "c", "type": "abstain"},
    ]

    report = score_submission(truths, submissions)

    assert (report.total, report.correct, report.wrong, report.abstained) == (4, 1, 1, 2)
    assert report.score == 0
    assert report.by_type["number"] == {"correct": 1, "wrong": 1, "abstained": 0, "score": 0}
    assert report.by_type["boolean"] == {"correct": 0, "wrong": 0, "abstained": 1, "score": 0}


def test_report_lists_submissions_for_unknown_questions_and_gives_no_points() -> None:
    report = score_submission([truth("number", 1, id_="a")], [submit("number", 1, id_="ghost")])

    assert report.score == 0
    assert report.abstained == 1
    assert report.unknown_ids == ["ghost"]


def test_duplicate_submission_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        score_submission([truth("number", 1)], [submit("number", 1), submit("number", 1)])


def test_duplicate_truth_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        score_submission([truth("number", 1), truth("number", 1)], [])


def test_submission_without_an_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="id"):
        score_submission([truth("number", 1)], [{"type": "number", "answer": 1}])


def test_load_jsonl_reads_rows_and_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": "a"}\n\n{"id": "b"}\n', encoding="utf-8")

    assert load_jsonl(path) == [{"id": "a"}, {"id": "b"}]


@pytest.mark.parametrize("line", ["not json", "[1, 2]"])
def test_load_jsonl_names_the_bad_line(tmp_path: Path, line: str) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(f'{{"id": "a"}}\n{line}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"rows\.jsonl:2"):
        load_jsonl(path)


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_cli_prints_a_summary_without_answers(tmp_path: Path) -> None:
    answers = write_jsonl(tmp_path / "answers.jsonl", [truth("string", "Bí mật")])
    submission = write_jsonl(tmp_path / "submission.jsonl", [submit("string", "Khác")])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "eval.scorer.cli",
            "--answers",
            str(answers),
            "--submission",
            str(submission),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["score"] == -1
    assert summary["wrong"] == 1
    assert "Bí mật" not in result.stdout


def test_cli_reports_a_bad_submission_file(tmp_path: Path) -> None:
    answers = write_jsonl(tmp_path / "answers.jsonl", [truth("number", 1)])
    submission = tmp_path / "submission.jsonl"
    submission.write_text("not json\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "eval.scorer.cli",
            "--answers",
            str(answers),
            "--submission",
            str(submission),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "submission.jsonl:1" in result.stderr
    assert "Traceback" not in result.stderr
