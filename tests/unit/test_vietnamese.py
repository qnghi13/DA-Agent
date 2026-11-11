"""Vietnamese shorthand and unconverted Telex typing are expanded by code, next to the original."""

import pytest

from da_agent.agent.prompts import build_user_prompt
from da_agent.agent.vietnamese import expand_question


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Cho tôi biết số đơn hqua", "hôm qua"),
        ("tổng doanh thu hnay", "hôm nay"),
        ("dthu thang 3 la bnhiu", "doanh thu"),
        ("dthu thang 3 la bnhiu", "bao nhiêu"),
        ("tháng này bán được bn", "bao nhiêu"),
        ("so với tháng trc", "tháng trước"),
        ("bán dc mấy đơn", "được"),
        ("top 3 sp là những j z", "những gì vậy"),
        ("tong doanh thu bua qua la bao nhieu", "hôm qua"),
        ("check giùm bữa qua cửa hàng bán được bao nhiêu tiền", "hôm qua"),
    ],
)
def test_shorthand_is_expanded(question: str, expected: str) -> None:
    assert expected in expand_question(question)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("revenue tuần rồi của cuwar hàng laf bnhiu", "của cửa hàng là bao nhiêu"),
        ("trong tháng 2 nawm 2026 cửa hàng bán được maays đĩa", "năm 2026"),
        ("trong tháng 2 nawm 2026 cửa hàng bán được maays đĩa", "mấy đĩa"),
        ("dthu nhóm hàng sắt thép chiếm nhiêu % toongr doanh thu", "tổng doanh thu"),
        ("tháng này có bao nhiêu ddown", "đơn"),
        ("soos tieenf thu về", "số tiền"),
        ("mặt hàng bans chạy nhaats", "bán chạy nhất"),
        ("vaayj thì sao", "vậy"),
        ("thangs 5", "tháng 5"),
        ("quas hạn", "quá hạn"),
        ("ddaau", "đâu"),
        ("nguwowif mua", "người"),
    ],
)
def test_unconverted_telex_is_decoded(question: str, expected: str) -> None:
    assert expected in expand_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "Strepsils Cool hộp 10 vỉ x 20 viên bán được bao nhiêu?",
        "check revenue top 5 tháng 3",
        "Panadol Extra, Vitamin C 500mg, Tiger lon 330ml",
        "Lavie 5l, Siro ho Prospan 100ml",
        "Doanh thu tháng 7/2025 là bao nhiêu?",
        "bán được 25k",
        "Gas Petrolimex 12kg, bếp gas đôi",
        "Plusssz Max Multivitamin viên sủi",
        "Tồn kho Air-X 80mg",
    ],
)
def test_names_and_plain_text_are_left_alone(question: str) -> None:
    assert expand_question(question) == question


def test_the_prompt_keeps_the_original_and_adds_the_expansion() -> None:
    prompt = build_user_prompt("Cho tôi biết số đơn hqua", "2026-04-14")
    assert "Cho tôi biết số đơn hqua" in prompt
    assert "Cho tôi biết số đơn hôm qua" in prompt


def test_the_prompt_adds_nothing_when_there_is_nothing_to_expand() -> None:
    prompt = build_user_prompt("Doanh thu hôm qua?", "2026-04-14")
    assert prompt.count("Doanh thu hôm qua?") == 1
