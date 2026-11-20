"""Comparing a golden case's expected answer with a rerun's (ADR 0043)."""

import pytest

from da_agent.agent.compare import compare_answers


def answer(kind: str, value: object = None) -> dict:
    return {"kind": kind, "value": value, "reason": None}


@pytest.mark.parametrize(
    ("expected", "actual", "outcome"),
    [
        (answer("number", 1000.0), answer("number", 1000.05), "same"),
        (answer("number", 1000.0), answer("number", 1001.0), "changed"),
        (answer("number", 0), answer("number", 0.0), "same"),
        (answer("percent", 12.5), answer("percent", 12.5), "same"),
        (answer("text", " Coffee "), answer("text", "coffee"), "same"),
        (answer("text", "Coffee"), answer("text", "Tea"), "changed"),
        (answer("date", "2026-06-01"), answer("date", "2026-06-01"), "same"),
        (answer("boolean", True), answer("boolean", False), "changed"),
        (answer("list", ["Tea", "Coffee"]), answer("list", ["coffee", "tea "]), "same"),
        (answer("list", ["Tea"]), answer("list", ["Tea", "Cake"]), "changed"),
        (answer("number", 5), answer("text", "5"), "changed"),
        (answer("unanswerable"), answer("unanswerable"), "same"),
        (answer("number", 5), None, "no_answer"),
        (answer("number", 5), answer("unanswerable"), "changed"),
    ],
)
def test_answers_compare_by_kind(expected: dict, actual: dict | None, outcome: str) -> None:
    assert compare_answers(expected, actual) == outcome


def test_tables_match_by_column_name_and_rows_in_any_order() -> None:
    expected = answer(
        "table", {"columns": ["product", "total"], "rows": [["Tea", 40], ["Coffee", 90]]}
    )
    reordered = answer(
        "table", {"columns": ["total", "product"], "rows": [[90.00001, "coffee"], [40, "Tea"]]}
    )
    different = answer(
        "table", {"columns": ["product", "total"], "rows": [["Tea", 41], ["Coffee", 90]]}
    )
    renamed = answer("table", {"columns": ["item", "total"], "rows": [["Tea", 40], ["Coffee", 90]]})

    assert compare_answers(expected, reordered) == "same"
    assert compare_answers(expected, different) == "changed"
    assert compare_answers(expected, renamed) == "changed"


def test_a_malformed_answer_is_changed_not_an_error() -> None:
    assert (
        compare_answers(answer("table", {"columns": ["a"]}), answer("table", "oops")) == "changed"
    )
    assert compare_answers(answer("number", "abc"), answer("number", 1)) == "changed"


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (answer("list", [100231, 100234]), answer("list", [100232, 100233])),
        (answer("list", ["0912345678"]), answer("list", ["0912349999"])),
        (answer("list", ["007"]), answer("list", ["7"])),
        (
            answer("table", {"columns": ["order_id"], "rows": [[1000001]]}),
            answer("table", {"columns": ["order_id"], "rows": [[1000004]]}),
        ),
    ],
)
def test_ids_codes_and_phone_numbers_must_match_exactly(expected: dict, actual: dict) -> None:
    assert compare_answers(expected, actual) == "changed"


def test_floats_in_lists_and_tables_match_within_the_tolerance() -> None:
    assert compare_answers(answer("list", [1.23455]), answer("list", [1.234549])) == "same"
    assert compare_answers(answer("list", [1.2345]), answer("list", [1.2400])) == "changed"
    table = {"columns": ["product", "share"], "rows": [["Tea", 0.333333], ["Coffee", 0.666667]]}
    close = {"columns": ["product", "share"], "rows": [["Coffee", 0.6666671], ["Tea", 0.3333331]]}
    assert compare_answers(answer("table", table), answer("table", close)) == "same"
