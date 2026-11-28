"""Sheet families and the layout flags that send them to the layout stage."""

import re

from da_agent.ingestion.grid import Grid
from da_agent.ingestion.layout_findings import describe_sheet, find_families
from da_agent.ingestion.raw import SENSITIVE_MARK


def grid(source: str, rows: list[list[object]]) -> Grid:
    return Grid(source, [[None if c in (None, "") else str(c) for c in row] for row in rows])


CLEAN = [["Date", "Item", "Amount"], ["1/3", "rice", "100"], ["2/3", "fish", "80"]]


def only(findings, source):
    [finding] = [f for f in findings if source in f.sources]
    return finding


def test_a_clean_sheet_with_one_header_row_has_no_finding() -> None:
    assert find_families([grid("a.xlsx#S", CLEAN)]) == []


def test_side_by_side_tables_raise_regions() -> None:
    rows = [
        ["Date", "Item", "Amount", None, "Date", "Source", "Amount"],
        ["1/3", "rice", "100", None, "5/3", "salary", "900"],
        ["2/3", "fish", "80", None, "6/3", "gift", "50"],
    ]

    finding = only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S")

    assert "regions" in finding.flags
    assert finding.regions == 2


def test_two_header_rows_raise_header_rows() -> None:
    rows = [
        ["Slip", "Client", "DATE", None, "MONEY", None],
        [None, None, "Booked", "Due", "Rent", "Deposit"],
        ["P1", "An", "1/5", "4/5", "450000", "600000"],
    ]

    assert "header_rows" in only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S").flags


def test_day_numbers_raise_numeric_header() -> None:
    rows = [["No", "Name", *range(1, 8)], ["1", "An", "x", "x", "P", "x", "x", "x", "K"]]

    assert "numeric_header" in only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S").flags


def test_section_rows_raise_sections() -> None:
    rows = [
        ["Date", "Order", "Amount"],
        ["Period 1"],
        ["5/6", "O1", "100"],
        ["Period 2"],
        ["9/6", "O3", "90"],
    ]

    assert "sections" in only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S").flags


def test_key_value_lines_down_one_column_raise_one_column() -> None:
    rows = [["Receipts"]] + [[f"{key}: {n}"] for n in range(5) for key in ("Date", "Client")]
    rows += [["-----"]] * 3

    assert "one_column" in only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S").flags


def test_cells_holding_key_value_lines_raise_packed_cells() -> None:
    cell = "No: 1 | Name: An\nNo: 2 | Name: Binh"
    rows = [["Week", "Mon", "Tue"], ["1", cell, cell], ["2", cell, None], ["3", None, cell]]

    assert "packed_cells" in only(find_families([grid("a.xlsx#S", rows)]), "a.xlsx#S").flags


def test_titles_that_differ_across_a_family_raise_title_context() -> None:
    def sheet(subject: str) -> list[list[object]]:
        return [
            ["Class", "7A1", "Subject", subject],
            ["No", "Code", "Name", "TX1", "GK"],
            ["1", "S1", "An", "7", "8"],
            ["2", "S2", "Binh", "6", "9"],
        ]

    [finding] = find_families([grid("a.xlsx#S1", sheet("Math")), grid("a.xlsx#S2", sheet("Art"))])

    assert "title_context" in finding.flags
    assert "cell D1" in finding.context_candidates
    assert finding.varying_cells == ["D1"]


def test_a_title_that_is_the_same_on_every_sheet_raises_nothing() -> None:
    rows = [["SALES BOOK"], ["Report period: all"], [None], *CLEAN]

    assert find_families([grid("a.xlsx#T1", rows), grid("a.xlsx#T2", rows)]) == []
    assert find_families([grid("b.xlsx#Sheet1", rows)]) == []


def test_a_header_below_the_one_the_fast_path_takes_raises_header_moved() -> None:
    rows = [["WORKERS"], ["Name", "Nickname"], ["An", "Ti"], ["Binh", "Su"]]
    layout = describe_sheet(grid("a.xlsx#Sheet1", rows), fast_header_row=1)

    [finding] = find_families([layout])

    assert "header_moved" in finding.flags
    assert describe_sheet(grid("a.xlsx#Sheet1", rows), fast_header_row=2).flags == set()


def test_headers_that_differ_only_in_case_and_order_are_one_drifting_family() -> None:
    first = grid("a.xlsx#T1", [["Mã ĐH", "Ngày"], ["O1", "1/1"], ["O2", "2/1"]])
    second = grid("b.xlsx#T1", [["ngày", "mã đh"], ["3/1", "O3"], ["4/1", "O4"]])

    [finding] = find_families([first, second])

    assert finding.sources == ["a.xlsx#T1", "b.xlsx#T1"]
    assert "drifting_header" in finding.flags
    assert "file_name" in finding.context_candidates


def test_sheet_names_that_name_things_raise_sheet_context() -> None:
    rows = [["Date", "Item", "Amount"], ["1/1", "rice", "100"], ["2/1", "fish", "80"]]

    [finding] = find_families([grid("a.xlsx#Mr An", rows), grid("a.xlsx#Ms Binh", rows)])

    assert re.fullmatch(r"f[0-9a-f]{6}", finding.family)
    assert finding.flags == ["sheet_context"]
    assert "sheet_name" in finding.context_candidates


def test_sheet_names_that_are_months_raise_nothing() -> None:
    rows = [["Date", "Item", "Amount"], ["1/1", "rice", "100"], ["2/1", "fish", "80"]]
    names = ["T1.2025", "Tháng 02-2025", "03.2025", "T04_2025"]

    assert find_families([grid(f"a.xlsx#{name}", rows) for name in names]) == []


def test_a_huge_sheet_gives_a_bounded_preview() -> None:
    rows = [["Date", "Order", "Amount"]]
    for n in range(20_000):
        rows.append([f"Period {n}"] if n % 3 == 0 else ["5/6", f"O{n}", str(n)])

    finding = only(find_families([grid("a.xlsx#S", rows)], preview_cells=4000), "a.xlsx#S")

    rows = [line for line in finding.preview.splitlines() if re.match(r"^[A-Z]+\d+: ", line)]
    cells = sum(line.count("|") + 1 for line in rows)
    assert cells <= 4000
    assert len(finding.preview) < 4000 * 90


def test_phone_numbers_are_masked_in_the_preview() -> None:
    rows = [["CONTACTS"], ["Name", "Phone"], ["An", "0901234567"], ["Binh", "0912345678"]]

    finding = describe_sheet(grid("a.xlsx#S", rows))

    assert "0901234567" not in finding.preview
    assert SENSITIVE_MARK in finding.preview


def test_long_cells_are_cut_in_the_preview() -> None:
    rows = [["NOTES"], ["Date", "Note"], ["1/1", "x" * 500], ["2/1", "y"]]

    finding = describe_sheet(grid("a.xlsx#S", rows))

    assert "x" * 81 not in finding.preview
    assert "x" * 80 in finding.preview


def test_a_table_that_shares_most_but_not_all_of_its_columns_is_another_family() -> None:
    sales = ["Date", "Item", "Count", "Box", "Boxes", "Weight", "Price", "Amount", "Paid", "Note"]
    bought = ["Date", "Garden", "Item", "Count", "Box", "Boxes", "Weight", "Price", "Amount"]
    rows_a = [sales, ["1/1"] + ["x"] * 9, ["2/1"] + ["y"] * 9]
    rows_b = [bought, ["1/1"] + ["x"] * 8, ["2/1"] + ["y"] * 8]

    findings = find_families([grid("a.xlsx#Mr An", rows_a), grid("a.xlsx#Garden buys", rows_b)])

    assert findings == []


def test_a_sheet_of_many_small_tables_lists_at_most_twenty_regions() -> None:
    rows: list[list[object]] = []
    for block in range(400):
        rows += [
            ["Date", "Item", "Amount"],
            ["1/1", f"x{block}", "1"],
            ["2/1", "y", "2"],
            [None],
            [None],
            [None],
        ]

    layout = describe_sheet(grid("a.xlsx#S", rows), preview_cells=4000)

    assert len(layout.bounds) <= 20
    assert len(layout.preview) < 4000 * 20
    assert sum(line.startswith("region ") for line in layout.preview.splitlines()) <= 20


def test_sensitive_values_inside_key_value_text_are_masked() -> None:
    rows = [["Receipts"]] + [
        ["Client: An"],
        ["Phone: 0901234567"],
        ["SĐT: 0912 345 678"],
        ["-----"],
    ] * 4
    packed = [["Week", "Mon"], ["1", "Name: An | Điện thoại: 0987654321\nName: Bình | Tip: 5k"]] * 4

    lines = describe_sheet(grid("a.xlsx#S", rows)).preview
    cells = describe_sheet(grid("b.xlsx#S", packed)).preview

    for number in ("0901234567", "0912 345 678", "0987654321"):
        assert number not in lines + cells
    assert "Client: An" in lines and "Tip: 5k" in cells


def test_a_family_keeps_its_id_when_a_drifted_sheet_sorts_first() -> None:
    title, period = ["ORDERS EXPORT"], ["Period 1"]
    a = grid("2025-02.xlsx#S", [title, ["Order", "Date", "Amount"], period, ["O1", "1/1", "1"]])
    b = grid("2025-03.xlsx#S", [title, ["Order", "Date", "Amount"], period, ["O2", "2/1", "2"]])
    c = grid(
        "2025-01.xlsx#S",
        [title, ["Order", "Date", "Amount", "Note"], period, ["O0", "1/1", "1", "x"]],
    )

    [before] = find_families([a, b])
    [after] = find_families([c, a, b])

    assert after.family == before.family


def test_rows_that_come_in_pairs_raise_paired_rows_with_a_hint() -> None:
    from tests.unit.test_grid import timesheet

    finding = only(find_families([grid("a.xlsx#Times", timesheet())]), "a.xlsx#Times")

    assert "paired_rows" in finding.flags
    assert any("In/Out" in h and "In, Out" in h for h in finding.hints)
