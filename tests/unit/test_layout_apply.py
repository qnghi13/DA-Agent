"""Applying a family layout to one sheet's grid: tidy rows and coverage."""

from da_agent.ingestion.grid import Grid
from da_agent.ingestion.layout_apply import apply_family
from da_agent.ingestion.layout_spec import FamilyLayout


def grid(rows: list[list[object]], source: str = "book.xlsx#Math_HK1") -> Grid:
    return Grid(source, [[None if c in (None, "") else str(c) for c in row] for row in rows])


def layout(**values) -> FamilyLayout:
    return FamilyLayout.model_validate({"family": "f1", **values})


def records(applied) -> list[dict[str, str | None]]:
    return [dict(zip(applied.header, row.cells, strict=True)) for row in applied.rows]


SCORES = [
    ["Class", "7A2", "Subject", "Math"],
    [None, None, None, "TERM I", None, None, "TERM II", None, None],
    ["No", "Code", "Name", "TX1", "GK", "CK", "TX1", "GK", "CK"],
    ["1", "S1", "An", "7", "8", "6.5", "8", "7", "9"],
    ["2", "S2", "Binh", "5", "6", "7", "6", "6", "8"],
]


def test_header_layers_with_a_spread_label_join_into_names() -> None:
    applied = apply_family(grid(SCORES), layout(header=[{"row": 2, "spread": "right"}, {"row": 3}]))

    assert applied.header[:9] == [
        "No", "Code", "Name",
        "TERM I / TX1", "TERM I / GK", "TERM I / CK",
        "TERM II / TX1", "TERM II / GK", "TERM II / CK",
    ]  # fmt: skip
    assert records(applied)[0]["TERM II / CK"] == "9"


def test_code_found_header_layers_are_used_when_the_layout_names_none() -> None:
    applied = apply_family(grid(SCORES), layout())

    assert applied.header[3] == "TERM I / TX1"


def test_context_from_the_sheet_name_and_after_a_label_lands_on_every_row() -> None:
    applied = apply_family(
        grid(SCORES),
        layout(
            context=[
                {"name": "sheet", "source": "sheet_name"},
                {"name": "subject", "source": "after_label", "label": "subject"},
                {"name": "class", "source": "cell", "cell": "B1"},
            ]
        ),
    )

    rows = records(applied)
    assert [(r["sheet"], r["subject"], r["class"]) for r in rows] == [
        ("Math_HK1", "Math", "7A2"),
        ("Math_HK1", "Math", "7A2"),
    ]
    assert applied.coverage.share == 1.0


def test_section_rows_become_a_column_filled_down() -> None:
    rows = [
        ["Date", "Order", "Amount"],
        ["Period 1"],
        ["5/6", "O1", "100"],
        ["6/6", "O2", "120"],
        ["Period 2"],
        ["9/6", "O3", "90"],
    ]

    applied = apply_family(grid(rows), layout(sections="period"))

    assert [(r["Order"], r["period"]) for r in records(applied)] == [
        ("O1", "Period 1"),
        ("O2", "Period 1"),
        ("O3", "Period 2"),
    ]
    assert applied.coverage.share == 1.0


def test_titles_totals_and_footers_are_excluded_and_counted() -> None:
    rows = [
        ["INVOICES"],
        [None],
        ["Code", "Date", "Buyer", "Total"],
        ["A1", "1/1", "An", "100"],
        ["A2", "1/1", "Binh", "120"],
        [None, None, "Page 1/2"],
        ["Code", "Date", "Buyer", "Total"],
        ["A3", "2/1", "Chi", "80"],
        ["Cộng", None, None, "300"],
    ]

    applied = apply_family(grid(rows), layout())

    assert [r["Code"] for r in records(applied)] == ["A1", "A2", "A3"]
    assert applied.coverage.excluded == 1 + 1 + 4 + 2
    assert applied.coverage.share == 1.0


def test_a_second_region_nobody_took_is_left() -> None:
    rows = [
        ["Date", "Item", "Amount", None, "Date", "Source"],
        ["1/3", "rice", "100", None, "5/3", "salary"],
        ["2/3", "fish", "80", None, "6/3", "gift"],
    ]

    applied = apply_family(grid(rows), layout(region=1))

    assert applied.coverage.share == 9 / 15
    assert applied.coverage.left[:2] == ["E1", "F1"]


def test_a_context_cell_inside_the_records_counts_twice() -> None:
    applied = apply_family(
        grid(SCORES), layout(context=[{"name": "code", "source": "cell", "cell": "B4"}])
    )

    assert applied.coverage.twice == 1
    assert applied.coverage.share < 1.0


def test_records_carry_their_range_and_an_ordinal_in_reading_order() -> None:
    rows = [["H"] * 10, ["a"] * 10, ["b"] * 10, ["c"] * 10]

    applied = apply_family(grid(rows, "f.csv"), layout())

    assert [(r.ordinal, r.cell_range) for r in applied.rows] == [
        (1, "A2:J2"),
        (2, "A3:J3"),
        (3, "A4:J4"),
    ]


def test_blank_header_names_become_numbered_columns() -> None:
    rows = [["Date", None, "Amount"], ["1/3", "x", "100"]]

    applied = apply_family(grid(rows), layout(header=[{"row": 1}]))

    assert applied.header == ["Date", "column_2", "Amount"]


def test_a_missing_region_gives_no_rows_and_leaves_every_cell() -> None:
    applied = apply_family(grid(SCORES), layout(region=5))

    assert applied.rows == []
    assert applied.coverage.share == 0.0


ATTENDANCE = [
    ["Class", "7A1", "Month", "9/2025"],
    ["No", "Name", "1", "2", "3", "4", "5"],
    ["1", "An", "x", None, "P", "x", "x"],
    ["2", "Binh", "x", "K", None, None, "x"],
]


def test_unpivot_turns_day_columns_into_rows_and_skips_blank_marks() -> None:
    applied = apply_family(
        grid(ATTENDANCE),
        layout(
            reshape={"kind": "unpivot", "first": "1", "last": "5", "key": "day", "value": "mark"}
        ),
    )

    assert applied.header == ["No", "Name", "day", "mark"]
    assert [(r["Name"], r["day"], r["mark"]) for r in records(applied)] == [
        ("An", "1", "x"), ("An", "3", "P"), ("An", "4", "x"), ("An", "5", "x"),
        ("Binh", "1", "x"), ("Binh", "2", "K"), ("Binh", "5", "x"),
    ]  # fmt: skip
    assert applied.rows[1].cell_range == "E3"
    assert [r.ordinal for r in applied.rows] == list(range(1, 8))
    assert applied.coverage.share == 1.0


def test_split_groups_gives_one_row_per_record_and_group() -> None:
    applied = apply_family(
        grid(SCORES),
        layout(
            header=[{"row": 2, "spread": "right"}, {"row": 3}],
            reshape={"kind": "split_groups", "layer": 1, "name": "term"},
        ),
    )

    assert applied.header == ["No", "Code", "Name", "term", "TX1", "GK", "CK"]
    assert [tuple(r.values()) for r in records(applied)] == [
        ("1", "S1", "An", "TERM I", "7", "8", "6.5"),
        ("1", "S1", "An", "TERM II", "8", "7", "9"),
        ("2", "S2", "Binh", "TERM I", "5", "6", "7"),
        ("2", "S2", "Binh", "TERM II", "6", "6", "8"),
    ]
    assert applied.rows[1].cell_range == "G4:I4"


def test_split_groups_skips_a_group_with_no_values() -> None:
    rows = [*SCORES[:4], ["2", "S2", "Binh", "5", "6", "7", None, None, None]]

    applied = apply_family(
        grid(rows),
        layout(
            header=[{"row": 2, "spread": "right"}, {"row": 3}],
            reshape={"kind": "split_groups", "layer": 1, "name": "term"},
        ),
    )

    assert [(r["Code"], r["term"]) for r in records(applied)] == [
        ("S1", "TERM I"), ("S1", "TERM II"), ("S2", "TERM I"),
    ]  # fmt: skip


TIMES = [
    ["Name", "In/Out", "1", "2", "3"],
    ["An", "In", "07:58", None, "08:01"],
    [None, "Out", "17:02", None, "17:30"],
    ["Binh", "In", "08:10", "08:00", None],
    [None, "Out", "17:00", "16:55", None],
]


def test_pair_rows_merges_each_first_row_with_the_rows_below_it() -> None:
    applied = apply_family(
        grid(TIMES),
        layout(
            header=[{"row": 1}],
            reshape={"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
        ),
    )

    assert applied.header == ["Name", "1 / In", "1 / Out", "2 / In", "2 / Out", "3 / In", "3 / Out"]
    assert records(applied)[0] == {
        "Name": "An", "1 / In": "07:58", "1 / Out": "17:02", "2 / In": None, "2 / Out": None,
        "3 / In": "08:01", "3 / Out": "17:30",
    }  # fmt: skip
    assert applied.rows[0].cell_range == "A2:E3"
    assert applied.coverage.share == 1.0


def test_paired_rows_then_unpivot_give_one_row_per_person_and_day() -> None:
    spec = layout(
        header=[{"row": 1}],
        reshape={"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
        then={"kind": "unpivot", "first": "1", "last": "3", "key": "day", "value": "time"},
    )

    applied = apply_family(grid(TIMES), spec)

    assert applied.header == ["Name", "day", "In", "Out"]
    assert [tuple(r.values()) for r in records(applied)] == [
        ("An", "1", "07:58", "17:02"),
        ("An", "3", "08:01", "17:30"),
        ("Binh", "1", "08:10", "17:00"),
        ("Binh", "2", "08:00", "16:55"),
    ]
    assert applied.rows[0].cell_range == "C2:C3"


def test_a_pair_whose_second_row_is_missing_is_kept_with_blanks() -> None:
    rows = [*TIMES[:2], TIMES[3], TIMES[4]]

    applied = apply_family(
        grid(rows),
        layout(
            header=[{"row": 1}],
            reshape={"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
        ),
    )

    assert [(r["Name"], r["1 / In"], r["1 / Out"]) for r in records(applied)] == [
        ("An", "07:58", None),
        ("Binh", "08:10", "17:00"),
    ]


LINES = [
    ["Receipts"],
    ["Date: 01/01/2026"],
    ["Client: An"],
    ["Service: cut"],
    ["Time: 09:30"],
    ["-----"],
    ["Date: 02/01/2026"],
    ["Service: wash"],
    ["-----"],
]


def one_column(lines: list[str]) -> list[list[str]]:
    return [[line] for line in lines]


def test_record_blocks_give_one_row_per_record_in_first_seen_key_order() -> None:
    applied = apply_family(
        grid(LINES), layout(reshape={"kind": "record_blocks", "starts_with": "Date"})
    )

    assert applied.header == ["Date", "Client", "Service", "Time"]
    assert [tuple(r.values()) for r in records(applied)] == [
        ("01/01/2026", "An", "cut", "09:30"),
        ("02/01/2026", None, "wash", None),
    ]
    assert applied.rows[0].cell_range == "A2:A5"
    assert applied.coverage.share == 1.0


def test_lines_before_the_opening_key_are_shared_by_every_record_of_the_group() -> None:
    lines = [
        ["Date: 03/01/2026"],
        ["Client: Binh"],
        ["Service: cut"],
        ["Price: 60k"],
        ["Service: dye"],
        ["Price: 400k"],
        ["-----"],
    ]

    applied = apply_family(
        grid(lines), layout(reshape={"kind": "record_blocks", "starts_with": "Service"})
    )

    assert [tuple(r.values()) for r in records(applied)] == [
        ("03/01/2026", "Binh", "cut", "60k"),
        ("03/01/2026", "Binh", "dye", "400k"),
    ]


def test_a_line_without_a_key_inside_a_record_goes_to_a_text_column() -> None:
    lines = [["Date: 1/1"], ["note without key"], ["-----"]]

    applied = apply_family(
        grid(lines), layout(reshape={"kind": "record_blocks", "starts_with": "Date"})
    )

    assert records(applied) == [{"Date": "1/1", "text": "note without key"}]


CALENDAR = [
    ["Week", "Mon", "Tue"],
    ["1", "No: 1 | Name: An\nNo: 2 | Name: Binh", "No: 3 | Name: Chi"],
    ["2", None, "No: 4 | Name: Dung\nholiday"],
]


def test_split_cell_gives_one_row_per_line_with_its_row_and_column_labels() -> None:
    applied = apply_family(
        grid(CALENDAR), layout(reshape={"kind": "split_cell", "name": "weekday"})
    )

    assert applied.header == ["Week", "weekday", "No", "Name", "text"]
    assert [tuple(r.values()) for r in records(applied)] == [
        ("1", "Mon", "1", "An", None),
        ("1", "Mon", "2", "Binh", None),
        ("1", "Tue", "3", "Chi", None),
        ("2", "Tue", "4", "Dung", None),
        ("2", "Tue", None, None, "holiday"),
    ]
    assert applied.rows[2].cell_range == "C2"
    assert applied.coverage.share == 1.0


def test_unpivot_to_a_last_column_the_sheet_lacks_runs_through_its_like_named_columns() -> None:
    applied = apply_family(
        grid([*ATTENDANCE[:2], ATTENDANCE[2]]),
        layout(
            reshape={"kind": "unpivot", "first": "1", "last": "31", "key": "day", "value": "mark"}
        ),
    )

    assert [r["day"] for r in records(applied)] == ["1", "3", "4", "5"]
    assert applied.problems == []


def test_split_groups_spreads_its_group_labels_even_when_the_layout_says_none() -> None:
    applied = apply_family(
        grid(SCORES),
        layout(
            header=[{"row": 2, "spread": "none"}, {"row": 3}],
            reshape={"kind": "split_groups", "layer": 1, "name": "term"},
        ),
    )

    assert applied.header == ["No", "Code", "Name", "term", "TX1", "GK", "CK"]
    assert len(applied.rows) == 4


def test_a_key_that_repeats_inside_a_record_is_reported_not_silently_dropped() -> None:
    lines = [
        ["Date: 03/01/2026"],
        ["Service: cut"],
        ["Price: 60k"],
        ["Service: dye"],
        ["Price: 400k"],
        ["-----"],
    ]

    applied = apply_family(
        grid(lines), layout(reshape={"kind": "record_blocks", "starts_with": "Date"})
    )

    assert len(applied.rows) == 1
    assert any("Service" in p and "repeat" in p for p in applied.problems)


def test_split_groups_on_the_column_names_themselves_is_refused() -> None:
    applied = apply_family(
        grid(SCORES),
        layout(header=[{"row": 3}], reshape={"kind": "split_groups", "layer": 1, "name": "term"}),
    )

    assert len(applied.rows) == 2
    assert any("split_groups" in p and "above" in p for p in applied.problems)


def test_a_record_key_that_no_line_has_is_answered_with_the_keys_found() -> None:
    applied = apply_family(
        grid(LINES), layout(reshape={"kind": "record_blocks", "starts_with": "Receipts"})
    )

    [problem] = applied.problems
    assert "Date" in problem and "Service" in problem


def test_page_footers_are_never_records_even_when_the_layout_keeps_footers() -> None:
    rows = [
        ["Code", "Date", "Buyer", "Total"],
        ["A1", "1/1", "An", "100"],
        ["A2", "1/1", "Binh", "120"],
        [None, None, "Page 1/2"],
        ["A3", "2/1", "Chi", "80"],
    ]

    applied = apply_family(grid(rows), layout(exclude=["title", "total", "blank"]))

    assert [r["Code"] for r in records(applied)] == ["A1", "A2", "A3"]


def test_a_missing_record_key_lists_keys_found_in_every_cell_and_part() -> None:
    applied = apply_family(
        grid(CALENDAR), layout(reshape={"kind": "record_blocks", "starts_with": "Order"})
    )

    [problem] = applied.problems
    assert "No" in problem and "Name" in problem


def test_a_timesheet_reads_as_one_row_per_person_and_day() -> None:
    from tests.unit.test_grid import timesheet

    spec = layout(
        sections="department",
        reshape={"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
        then={"kind": "unpivot", "first": "1", "last": "31", "key": "day", "value": "time"},
    )

    applied = apply_family(grid(timesheet()), spec)

    assert len(applied.rows) == 3 * 3 * 7
    first = records(applied)[0]
    assert (first["No"], first["department"], first["day"], first["In"], first["Out"]) == (
        "E00", "DEPARTMENT 0", "1", "07:10", "16:40",
    )  # fmt: skip
    assert applied.coverage.share == 1.0
