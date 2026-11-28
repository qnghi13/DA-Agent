"""The declarative layout spec a model proposes, and the checks code runs on it."""

import pytest
from pydantic import ValidationError

from da_agent.ingestion.layout_spec import LayoutSpec, check_layout
from da_agent.ingestion.report import FamilyFinding


def finding(family: str = "f1", **changes) -> FamilyFinding:
    values = {
        "family": family,
        "sources": ["a.xlsx#T1"],
        "flags": ["header_rows"],
        "regions": 2,
        "region_bounds": ["A1:O48", "Q1:R3"],
        "columns": ["STT", "MA HS", "HỌC KỲ I", "HỌC KỲ II", "TX1", "CK", "1", "31", "In/Out"],
    }
    return FamilyFinding(**(values | changes))


VALID = {
    "families": [
        {
            "family": "f1",
            "region": 1,
            "header": [{"row": 3, "spread": "right"}, {"row": 4}],
            "context": [
                {"name": "class", "source": "cell", "cell": "B2"},
                {"name": "month", "source": "sheet_name"},
                {"name": "subject", "source": "after_label", "label": "Môn"},
            ],
            "sections": "period",
            "reshape": {
                "kind": "unpivot",
                "first": "1",
                "last": "31",
                "key": "day",
                "value": "mark",
            },
        }
    ]
}


def test_a_valid_spec_parses_and_passes_the_checks() -> None:
    spec = LayoutSpec.model_validate(VALID)

    checked, problems = check_layout(spec, [finding()])

    assert problems == []
    assert checked == spec
    assert spec.families[0].exclude == ["title", "total", "footer", "blank"]


def test_every_reshape_kind_parses() -> None:
    reshapes = [
        {"kind": "split_groups", "layer": 1, "name": "term"},
        {"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
        {"kind": "record_blocks", "starts_with": "Date"},
        {"kind": "split_cell"},
    ]
    for reshape in reshapes:
        spec = LayoutSpec.model_validate({"families": [{"family": "f1", "reshape": reshape}]})
        assert spec.families[0].reshape is not None


def test_unknown_fields_and_bad_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LayoutSpec.model_validate({"families": [{"family": "f1", "code": "import os"}]})
    with pytest.raises(ValidationError):
        LayoutSpec.model_validate(
            {
                "families": [
                    {"family": "f1", "context": [{"name": "drop table", "source": "sheet_name"}]}
                ]
            }
        )


def test_sizes_are_bounded() -> None:
    too_many = [{"name": f"c{i}", "source": "sheet_name"} for i in range(9)]
    with pytest.raises(ValidationError):
        LayoutSpec.model_validate({"families": [{"family": "f1", "context": too_many}]})
    with pytest.raises(ValidationError):
        LayoutSpec.model_validate(
            {"families": [{"family": "f1", "header": [{"row": n} for n in range(1, 5)]}]}
        )
    with pytest.raises(ValidationError):
        LayoutSpec.model_validate(
            {
                "families": [
                    {
                        "family": "f1",
                        "reshape": {"kind": "pair_rows", "column": "x", "values": ["a"]},
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"family": "f9"}, "f9"),
        ({"family": "f1", "region": 3}, "region 3"),
        ({"family": "f1", "region": 2, "header": [{"row": 4}]}, "row 4"),
        (
            {
                "family": "f1",
                "reshape": {
                    "kind": "unpivot",
                    "first": "1",
                    "last": "Day 31",
                    "key": "d",
                    "value": "v",
                },
            },
            "Day 31",
        ),
        (
            {
                "family": "f1",
                "reshape": {"kind": "pair_rows", "column": "Kind", "values": ["In", "Out"]},
            },
            "Kind",
        ),
        ({"family": "f1", "context": [{"name": "c", "source": "cell"}]}, "cell"),
        ({"family": "f1", "context": [{"name": "c", "source": "cell", "cell": "here"}]}, "here"),
        ({"family": "f1", "context": [{"name": "c", "source": "after_label"}]}, "label"),
    ],
)
def test_an_invalid_family_entry_is_dropped_with_a_problem(entry: dict, reason: str) -> None:
    spec = LayoutSpec.model_validate({"families": [entry, {"family": "f2"}]})

    checked, problems = check_layout(spec, [finding(), finding("f2")])

    assert [f.family for f in checked.families] == ["f2"]
    assert len(problems) == 1
    assert reason in problems[0]


def test_joined_header_names_count_as_known_columns() -> None:
    entry = {
        "family": "f1",
        "reshape": {
            "kind": "unpivot",
            "first": "HỌC KỲ I / TX1",
            "last": "học kỳ ii / ck",
            "key": "k",
            "value": "v",
        },
    }

    checked, problems = check_layout(LayoutSpec.model_validate({"families": [entry]}), [finding()])

    assert problems == []
    assert len(checked.families) == 1


def test_a_family_region_named_twice_keeps_the_first_entry() -> None:
    spec = LayoutSpec.model_validate(
        {"families": [{"family": "f1"}, {"family": "f1", "sections": "x"}]}
    )

    checked, problems = check_layout(spec, [finding()])

    assert [f.sections for f in checked.families] == [None]
    assert "twice" in problems[0]


def test_a_family_may_have_one_entry_per_region() -> None:
    spec = LayoutSpec.model_validate(
        {"families": [{"family": "f1"}, {"family": "f1", "region": 2}]}
    )

    checked, problems = check_layout(spec, [finding()])

    assert problems == []
    assert [f.region for f in checked.families] == [1, 2]


def test_a_header_name_that_contains_a_slash_is_known() -> None:
    entry = {
        "family": "f1",
        "reshape": {"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
    }

    checked, problems = check_layout(LayoutSpec.model_validate({"families": [entry]}), [finding()])

    assert problems == []
    assert len(checked.families) == 1
