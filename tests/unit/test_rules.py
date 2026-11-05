"""On-demand rule documents: splitting into sections and searching them."""

from da_agent.agent.loop_types import Document
from da_agent.agent.rules import RuleIndex, split_sections

RULES = """\
# Rulebook

Preface rule that applies everywhere.

## 1. Đơn hàng và doanh thu
Doanh thu không tính đơn huỷ.

## 4. Công nợ
Nợ của khách là tổng tiền chưa trả.

## 6. Theo ngành
### Nhà thuốc
Thuốc kê đơn là sản phẩm ghi kê đơn.
"""


def index() -> RuleIndex:
    return RuleIndex([Document("rules.md", RULES, on_demand=True)])


def test_split_keeps_the_preface_and_nests_subsections_in_their_section() -> None:
    preface, sections = split_sections(RULES)

    assert "Preface rule" in preface
    assert [s.title for s in sections] == [
        "1. Đơn hàng và doanh thu",
        "4. Công nợ",
        "6. Theo ngành",
    ]
    assert "Thuốc kê đơn" in sections[2].text


def test_search_matches_titles_without_diacritics_or_case() -> None:
    assert [s.title for s in index().search("CONG NO")] == ["4. Công nợ"]


def test_search_matches_a_section_number() -> None:
    assert [s.title for s in index().search("4")] == ["4. Công nợ"]


def test_search_ranks_sections_by_keywords_in_their_text() -> None:
    found = index().search("thuốc kê đơn")

    assert found[0].title == "6. Theo ngành"


def test_search_returns_nothing_for_unrelated_words() -> None:
    assert index().search("zebra") == []


def test_only_on_demand_documents_are_indexed() -> None:
    inline = RuleIndex([Document("rules.md", RULES)])

    assert inline.sections == []
