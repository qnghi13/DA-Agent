"""Regions and row roles found by code in a sheet's grid."""

from da_agent.ingestion.grid import Grid, Region, find_regions, row_roles


def grid(rows: list[list[object]], first_row: int = 1) -> Grid:
    return Grid(
        source="f.xlsx#S",
        cells=[[None if c in (None, "") else str(c) for c in row] for row in rows],
        first_row=first_row,
    )


def whole(g: Grid) -> Region:
    return Region(top=0, left=0, bottom=len(g.cells) - 1, right=max(len(r) for r in g.cells) - 1)


def test_addresses_use_sheet_numbering() -> None:
    g = grid([["a"]], first_row=3)

    assert g.address(0, 0) == "A3"
    assert g.address(9, 1) == "B12"
    assert g.address(0, 27) == "AB3"


def test_two_tables_split_by_one_empty_column_are_two_regions() -> None:
    g = grid(
        [
            ["Date", "Item", "Amount", None, "Date", "Source"],
            ["1/3", "rice", "100", None, "5/3", "salary"],
            ["2/3", "fish", "80", None, None, None],
        ]
    )

    assert find_regions(g) == [Region(0, 0, 2, 2), Region(0, 4, 1, 5)]


def test_a_title_above_a_table_joins_its_region() -> None:
    g = grid(
        [
            ["SPENDING", None, None, None, "INCOME"],
            [None, None, None, None, None],
            ["Date", "Item", "Amount", None, "Date", "Source"],
            ["1/3", "rice", "100", None, "5/3", "salary"],
        ]
    )

    assert find_regions(g) == [Region(0, 0, 3, 2), Region(0, 4, 3, 5)]


def test_a_title_and_an_empty_row_come_before_the_header() -> None:
    g = grid(
        [
            ["INVOICE LIST"],
            [None, None, None],
            ["Code", "Date", "Total"],
            ["A1", "1/1/2025", "100"],
            ["A2", "2/1/2025", "250"],
        ]
    )

    assert row_roles(g, whole(g)) == ["title", "blank", "header", "data", "data"]


def test_day_numbers_make_a_header_row() -> None:
    g = grid(
        [
            ["Class", "7A1", "Month", "9/2025"],
            ["No", "Name", *range(1, 11)],
            ["1", "An", "x", "x", None, "P", "x", "x", "x", "K", "x", "x"],
            ["2", "Binh", "x", None, "x", "x", "x", "x", "x", "x", "x", "x"],
        ]
    )

    assert row_roles(g, whole(g)) == ["title", "header", "data", "data"]


def test_one_cell_rows_between_records_are_sections() -> None:
    g = grid(
        [
            ["Date", "Order", "Kind", "Amount"],
            ["Period 01/06/2025"],
            ["5/6", "O1", "sale", "100"],
            ["6/6", "O2", "sale", "120"],
            ["Period 08/06/2025"],
            ["9/6", "O3", "sale", "90"],
        ]
    )

    assert row_roles(g, whole(g)) == ["header", "section", "data", "data", "section", "data"]


def test_a_row_that_sums_the_records_above_is_a_total() -> None:
    g = grid(
        [
            ["Date", "Item", "Amount"],
            ["1/3", "rice", "100"],
            ["2/3", "fish", "80"],
            [None, None, "180"],
        ]
    )

    assert row_roles(g, whole(g)) == ["header", "data", "data", "total"]


def test_a_labelled_total_row_is_a_total() -> None:
    g = grid(
        [
            ["Date", "Item", "Amount"],
            ["1/3", "rice", "100k"],
            ["2/3", "fish", "80k"],
            ["Cộng", None, "180k"],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "total"


def test_a_page_marker_after_the_records_is_a_footer() -> None:
    g = grid(
        [
            ["Code", "Date", "Buyer", "Total"],
            ["A1", "1/1", "An", "100"],
            ["A2", "1/1", "Binh", "120"],
            [None, None, "Page 3/10"],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "footer"


def test_group_labels_with_blanks_between_sit_in_the_header() -> None:
    g = grid(
        [
            ["Class", "7A2", "Subject", "Math"],
            [None, None, None, "TERM I", None, None, "TERM II", None, None],
            ["No", "Code", "Name", "TX1", "GK", "CK", "TX1", "GK", "CK"],
            ["1", "S1", "An", "7", "8", "6.5", "8", "7", "9"],
        ]
    )

    assert row_roles(g, whole(g)) == ["title", "header", "header", "data"]


def test_sub_labels_under_a_spread_label_sit_in_the_header() -> None:
    g = grid(
        [
            ["Slip", "Client", "DATE", None, None, "MONEY", None],
            [None, None, "Booked", "Taken", "Due", "Rent", "Deposit"],
            ["P1", "An", "1/5", "2/5", "4/5", "450000", "600000"],
            ["P1", None, None, None, None, "200000", None],
        ]
    )

    assert row_roles(g, whole(g)) == ["header", "header", "data", "data"]


def test_a_header_repeated_after_a_page_break_is_a_header() -> None:
    g = grid(
        [
            ["Code", "Date", "Total"],
            ["A1", "1/1", "100"],
            ["Code", "Date", "Total"],
            ["A2", "2/1", "120"],
        ]
    )

    assert row_roles(g, whole(g)) == ["header", "data", "header", "data"]


def test_records_with_blank_first_cells_stay_data() -> None:
    g = grid(
        [
            ["Date", "Item", "Group", "Amount"],
            ["2/3", "rice", "market", "100"],
            [None, "fish", "market", "80"],
            [None, "tea", "drinks", "30k"],
        ]
    )

    assert row_roles(g, whole(g)) == ["header", "data", "data", "data"]


def test_a_narrow_metadata_row_above_a_wider_header_is_a_title() -> None:
    g = grid(
        [
            ["PAYMENT STATEMENT"],
            ["Currency: VND", "Negative: fees"],
            [None, None, None, None],
            ["Date", "Order", "Kind", "Amount"],
            ["Period 01/06/2025"],
            ["5/6", "O1", "sale", "100"],
            ["6/6", "O2", "sale", "120"],
        ]
    )

    roles = row_roles(g, find_regions(g)[0])

    assert roles[:4] == ["title", "title", "blank", "header"]


def test_blocks_below_a_table_with_its_columns_and_no_header_continue_it() -> None:
    g = grid(
        [
            ["Date", "Order", "Kind", "Amount"],
            ["Period 1"],
            ["5/6", "O1", "sale", "100"],
            ["6/6", "O2", "sale", "120"],
            [None],
            ["Period 2"],
            ["9/6", "O3", "sale", "90"],
            ["9/6", "O4", "fee", "-5"],
            ["10/6", "O5", "sale", "70"],
            ["11/6", "O6", "sale", "40"],
        ]
    )

    assert find_regions(g) == [Region(0, 0, 9, 3)]


def test_a_second_table_with_its_own_header_below_stays_apart() -> None:
    g = grid(
        [
            ["Date", "Item", "Amount"],
            ["1/3", "rice", "100"],
            ["2/3", "fish", "80"],
            [None],
            ["Name", "Phone", "Note"],
            ["An", "0901", "x"],
            ["Binh", "0902", "y"],
            ["Chi", "0903", "z"],
        ]
    )

    assert find_regions(g) == [Region(0, 0, 2, 2), Region(4, 0, 7, 2)]


def test_a_name_that_starts_like_a_total_label_is_data() -> None:
    g = grid(
        [
            ["Name", "Phone", "Address", "Note"],
            ["An", "0901 222 333", "1 Main St", "x"],
            ["Tống Văn Ba", "0900 111 222", "12 Main Street"],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "data"


def test_a_running_number_beside_text_is_not_a_total() -> None:
    g = grid(
        [
            ["No", "Name", "Mark"],
            ["1", "An", "x"],
            ["2", "Binh", "x"],
            ["3", "Chi", None],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "data"


def test_a_note_after_the_last_record_is_a_footer_not_a_section() -> None:
    g = grid(
        [
            ["Date", "Item", "Amount"],
            ["1/3", "rice", "100"],
            ["2/3", "fish", "80"],
            ["Note: written by hand"],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "footer"


def test_row_roles_stay_fast_on_a_large_sparse_sheet() -> None:
    import time

    rows: list[list[object]] = [["Date", "Item", "Amount", "Note"]]
    for n in range(20_000):
        rows.append([None, None, str(n)] if n % 2 else ["1/1", "rice", str(n), None])
    g = grid(rows)

    start = time.perf_counter()
    roles = row_roles(g, whole(g))
    assert time.perf_counter() - start < 2.0
    assert roles[0] == "header"


def test_find_regions_stays_fast_with_many_blocks() -> None:
    import time

    rows: list[list[object]] = []
    for _ in range(300):
        rows += [["Date", "Item", "Amount"], ["1/1", "rice", "1"], ["2/1", "fish", "2"],
                 ["3/1", "tea", "3"], [None]]  # fmt: skip
    g = grid(rows)

    start = time.perf_counter()
    find_regions(g)
    assert time.perf_counter() - start < 3.0


def timesheet(departments: int = 3, people: int = 3) -> list[list[object]]:
    """In and Out rows per person, a one-cell row per department, days as columns."""
    rows: list[list[object]] = [
        ["TIMESHEET"],
        ["No", "Name", "Dept", "Month", "In/Out", *[str(d) for d in range(1, 8)]],
    ]
    for d in range(departments):
        rows.append([f"DEPARTMENT {d}"])
        for p in range(people):
            rows.append([f"E{d}{p}", f"Person {d}{p}", f"Dept {d}", "2026-01-01", "In",
                         *["07:1" + str(k % 10) for k in range(7)]])  # fmt: skip
            rows.append([None, None, None, None, "Out", *["16:4" + str(k % 10) for k in range(7)]])
    return rows


def test_a_row_of_times_is_not_a_header() -> None:
    g = grid(timesheet()[3:9])

    assert "header" not in row_roles(g, whole(g))


def test_department_rows_do_not_split_a_timesheet_into_regions() -> None:
    g = grid(timesheet())

    assert find_regions(g) == [Region(0, 0, len(g.cells) - 1, 11)]


def test_one_cell_rows_that_are_most_of_the_table_are_records_not_sections() -> None:
    g = grid(
        [
            ["Short", "Full name"],
            ["BBL = sponge cake box"],
            ["BCN", "banana cake"],
            ["BDS = donut"],
            ["BKS 18cm = birthday cake 18cm"],
            ["BMT", "garlic bread"],
            ["DCS = candle"],
        ]
    )

    assert row_roles(g, whole(g)) == ["header"] + ["data"] * 6


def test_compact_money_and_a_trailing_number_only_row_make_a_total() -> None:
    g = grid(
        [
            ["Day", "Group", "Item", "Qty", "Total"],
            ["1/3", "BEER", "Lager", "7", "84k"],
            [None, "SODA", "Tea", "10", "100k"],
            ["2/3", "SOAP", "Paper", "4", "1tr2"],
            [None, None, None, None, "128tr450"],
        ]
    )

    assert row_roles(g, whole(g))[-1] == "total"
