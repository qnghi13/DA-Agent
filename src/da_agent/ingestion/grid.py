"""A sheet as a grid of cells, with the regions and row roles that code can find in it.

Regions are connected groups of filled cells. Row roles say which rows of a region are titles,
header layers, sections, totals, page footers, or records. Both are findings for the layout
stage: code proposes them, a layout spec confirms or overrides them, and code applies it.
"""

import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Literal

from da_agent.ingestion.profile import classify

RowRole = Literal["blank", "title", "header", "section", "total", "footer", "data"]

# A header row is at least this wide compared with the records under it, and mostly text.
HEADER_WIDTH_SHARE = 0.5
HEADER_TEXT_SHARE = 0.8
HEADER_LOOKAHEAD = 20
# A one-cell row is a section only under a header this wide: in a two-column table it is a
# record with one value missing (a list of abbreviations, a name without a phone).
SECTION_MIN_WIDTH = 3
# A header candidate is a metadata line when a wider candidate follows within this many rows.
HEADER_OUTGROWN_ROWS = 5
# A run of day numbers 1..n of this length makes a header even without text.
NUMBER_RUN = 5
# A small block (a title, a footer) within this many blank rows of a table joins its region.
ATTACH_ROWS = 3
ATTACH_GAP = 2
_TOTAL_LABEL = re.compile(r"^(cong|tong|total|sum|grand total)\b")
_PAGE_MARKER = re.compile(r"^[^\W\d_]+\.?\s+\d+\s*/\s*\d+$")
# A clock time ("07:10", "16:45:00") is a value, though the raw profile has no pattern for it.
_TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_TEXT_KINDS = {"text"}


@dataclass(frozen=True)
class Region:
    """0-based, inclusive bounds within the grid."""

    top: int
    left: int
    bottom: int
    right: int

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1


@dataclass
class Grid:
    source: str
    cells: list[list[str | None]]
    first_row: int = 1

    def address(self, row: int, col: int) -> str:
        return f"{column_letters(col)}{self.first_row + row}"

    def value(self, row: int, col: int) -> str | None:
        if 0 <= row < len(self.cells) and 0 <= col < len(self.cells[row]):
            value = self.cells[row][col]
            return value if value is not None and value.strip() else None
        return None

    def row(self, row: int, region: Region) -> list[str | None]:
        return [self.value(row, c) for c in range(region.left, region.right + 1)]


def column_letters(col: int) -> str:
    letters = ""
    col += 1
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def plain(text: str) -> str:
    """Lower case without accents, for matching labels."""
    text = text.strip().lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def find_regions(grid: Grid) -> list[Region]:
    """Connected groups of filled cells (4-neighbour), with overlapping boxes merged and small
    blocks just above or below a table joined to it. Sorted by top, then left."""
    filled = {
        (r, c)
        for r, row in enumerate(grid.cells)
        for c, value in enumerate(row)
        if value is not None and value.strip()
    }
    boxes: list[Region] = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(filled):
        if start in seen:
            continue
        stack, seen_here = [start], [start]
        seen.add(start)
        while stack:
            r, c = stack.pop()
            for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if (nr, nc) in filled and (nr, nc) not in seen:
                    seen.add((nr, nc))
                    stack.append((nr, nc))
                    seen_here.append((nr, nc))
        rows = [p[0] for p in seen_here]
        cols = [p[1] for p in seen_here]
        boxes.append(Region(min(rows), min(cols), max(rows), max(cols)))
    boxes = _merge_overlapping(boxes)
    boxes = _attach_small(grid, boxes)
    boxes = _join_continuations(grid, boxes)
    return sorted(boxes, key=lambda b: (b.top, b.left))


def _overlap(a: Region, b: Region) -> bool:
    return a.left <= b.right and b.left <= a.right and a.top <= b.bottom and b.top <= a.bottom


def _union(a: Region, b: Region) -> Region:
    return Region(
        min(a.top, b.top), min(a.left, b.left), max(a.bottom, b.bottom), max(a.right, b.right)
    )


def _merge_overlapping(boxes: list[Region]) -> list[Region]:
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _overlap(boxes[i], boxes[j]):
                    boxes[i] = _union(boxes[i], boxes[j])
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


def _widest_row(grid: Grid, block: Region) -> int:
    return max(len(_filled(grid.row(r, block))) for r in range(block.top, block.bottom + 1))


def _attach_small(grid: Grid, boxes: list[Region]) -> list[Region]:
    """Join each block of at most ATTACH_ROWS rows, none wider than half the table, to
    the nearest taller block that shares columns with it and sits within ATTACH_GAP blank rows
    above or below. Titles and page footers are such blocks; a short table is not."""
    changed = True
    while changed:
        changed = False
        for block in sorted(boxes, key=lambda b: b.height):
            if block.height > ATTACH_ROWS:
                break
            best, best_gap = None, None
            for table in boxes:
                if table.height <= block.height:
                    continue
                width = table.right - table.left + 1
                if _widest_row(grid, block) > max(1, HEADER_WIDTH_SHARE * width):
                    continue
                if block.left > table.right or table.left > block.right:
                    continue
                if block.bottom < table.top:
                    gap = table.top - block.bottom - 1
                else:
                    gap = block.top - table.bottom - 1
                if 0 <= gap <= ATTACH_GAP and (best_gap is None or gap < best_gap):
                    best, best_gap = table, gap
            if best is not None:
                boxes = [b for b in boxes if b is not block and b is not best]
                boxes = _merge_overlapping([*boxes, _union(best, block)])
                changed = True
                break
    return boxes


def _join_continuations(grid: Grid, boxes: list[Region]) -> list[Region]:
    """Join a block to the table just above it when it has the same columns and no header of
    its own: the table went on after a blank row."""
    boxes = sorted(boxes, key=lambda b: (b.top, b.left))
    headed: dict[Region, bool] = {}
    joined = True
    while joined:
        joined = False
        for upper in boxes:
            for lower in boxes:
                gap = lower.top - upper.bottom - 1
                if lower is upper or not 0 <= gap <= ATTACH_GAP:
                    continue
                if abs(lower.left - upper.left) > 1 or abs(lower.right - upper.right) > 1:
                    continue
                if lower not in headed:
                    headed[lower] = "header" in row_roles(grid, lower)
                if headed[lower]:
                    continue
                boxes = [b for b in boxes if b is not upper and b is not lower]
                boxes.append(_union(upper, lower))
                boxes.sort(key=lambda b: (b.top, b.left))
                joined = True
                break
            if joined:
                break
    return boxes


_MILLIONS = re.compile(r"^(-?\d+)\s*tr\s*(\d{0,3})\s*(đ|d|vnđ|vnd)?$", re.IGNORECASE)


def _number(text: str) -> float | None:
    """Plain, grouped, `k`/`đ`-suffixed, and compact million (`13tr2`, `128tr450`) numbers;
    anything else is not a number here."""
    stripped = text.strip()
    millions = _MILLIONS.match(stripped)
    if millions:
        whole, rest = int(millions.group(1)), millions.group(2)
        thousands = int(rest.ljust(3, "0")) if rest else 0
        sign = -1 if whole < 0 else 1
        return float(whole * 1_000_000 + sign * thousands * 1_000)
    kind = classify(stripped)
    if kind == "money_suffix":
        match = re.match(r"^(-?\d[\d.,]*)\s*(\D+)$", stripped)
        if not match:
            return None
        digits, unit = match.groups()
        value = _number(digits)
        if value is None:
            return None
        return value * 1000 if unit.strip().lower() == "k" else value
    if kind in ("integer", "decimal"):
        return float(stripped)
    if kind == "number_grouped":
        return float(re.sub(r"[,.]", "", stripped))
    return None


def _filled(cells: list[str | None]) -> list[str]:
    return [c for c in cells if c is not None]


def _is_text(cell: str) -> bool:
    return classify(cell) in _TEXT_KINDS and not _TIME.match(cell.strip())


def _text_share(cells: list[str]) -> float:
    return sum(_is_text(c) for c in cells) / len(cells) if cells else 0.0


def number_run(cells: list[str | None]) -> bool:
    """True when the row holds 1, 2, ..., n (n >= NUMBER_RUN) in consecutive columns."""
    run, expected = 0, 1
    for cell in cells:
        if cell is not None and cell.strip() == str(expected):
            run, expected = run + 1, expected + 1
            if run >= NUMBER_RUN:
                return True
        elif cell is not None and cell.strip() == "1":
            run, expected = 1, 2
        else:
            run, expected = 0, 1
    return False


def _header_like(row: list[str | None], typical_width: float) -> bool:
    cells = _filled(row)
    if len(cells) < 2 or len(cells) < HEADER_WIDTH_SHARE * typical_width:
        return False
    return _text_share(cells) >= HEADER_TEXT_SHARE or number_run(row)


def _upper_layer(row: list[str | None]) -> bool:
    """A group label row above a header: the first column is blank and every cell is text."""
    cells = _filled(row)
    return bool(cells) and row[0] is None and _text_share(cells) == 1.0


def _lower_layer(row: list[str | None], header: list[str | None]) -> bool:
    """A sub-label row under a header: every cell is text and some sit under a blank header
    cell, as under a label that spreads to its right."""
    cells = _filled(row)
    if not cells or _text_share(cells) < 1.0:
        return False
    return any(v is not None and (i >= len(header) or header[i] is None) for i, v in enumerate(row))


def _same_texts(a: list[str | None], b: list[str | None]) -> bool:
    return [plain(c) if c else "" for c in a] == [plain(c) if c else "" for c in b]


def row_roles(grid: Grid, region: Region) -> list[RowRole]:
    rows = [grid.row(r, region) for r in range(region.top, region.bottom + 1)]
    roles: list[RowRole] = ["blank" if not _filled(row) else "data" for row in rows]
    filled_at = [i for i, row in enumerate(rows) if _filled(row)]

    candidates = []
    for position, i in enumerate(filled_at[: HEADER_LOOKAHEAD + 1]):
        below = [
            len(_filled(rows[j])) for j in filled_at[position + 1 : position + 1 + HEADER_LOOKAHEAD]
        ]
        typical = statistics.median(below) if below else len(_filled(rows[i]))
        if _header_like(rows[i], typical):
            candidates.append(i)
    # A narrow metadata line ("Currency: VND") can pass as a header; the real header is the
    # first candidate that no candidate a few rows below it outgrows.
    main = None
    for k, i in enumerate(candidates):
        later = [
            j
            for j in candidates[k + 1 :]
            if j - i <= HEADER_OUTGROWN_ROWS and not (j == i + 1 and _lower_layer(rows[j], rows[i]))
        ]
        if not any(len(_filled(rows[j])) > len(_filled(rows[i])) for j in later):
            main = i
            break
    if main is None:
        return roles

    top = main
    roles[main] = "header"
    while top - 1 >= 0 and roles[top - 1] != "blank" and _upper_layer(rows[top - 1]):
        top -= 1
        roles[top] = "header"
    bottom = main
    while (
        bottom + 1 < len(rows)
        and roles[bottom + 1] != "blank"
        and _lower_layer(rows[bottom + 1], rows[bottom])
    ):
        bottom += 1
        roles[bottom] = "header"
    for i in range(top):
        if roles[i] != "blank":
            roles[i] = "title"

    header_width = len(_filled(rows[main]))
    sums = _RunningSums()
    for i in range(bottom + 1, len(rows)):
        if roles[i] == "blank":
            continue
        row, cells = rows[i], _filled(rows[i])
        if _same_texts(row, rows[main]):
            roles[i] = "header"
        elif len(cells) == 1 and _PAGE_MARKER.match(cells[0].strip()):
            roles[i] = "footer"
        elif _labelled_total(cells, header_width) or sums.matches(row):
            roles[i] = "total"
            sums = _RunningSums()
        elif (
            len(cells) == 1 and classify(cells[0]) in _TEXT_KINDS
            and header_width >= SECTION_MIN_WIDTH
        ):  # fmt: skip
            roles[i] = "section"
            sums = _RunningSums()
        else:
            sums.add(row)
    _trailing_total(rows, roles, header_width)
    # A section opens records; one with no record after it is a note at the foot of the table.
    for i in [k for k, role in enumerate(roles) if role == "section"]:
        following = next((r for r in roles[i + 1 :] if r not in ("blank", "footer")), None)
        if following != "data":
            roles[i] = "footer"
    return roles


def _labelled_total(cells: list[str], header_width: int) -> bool:
    """ "Cộng", "Tổng cộng", "Total" first, then at least one number, in a row narrower than the
    header. The number keeps a name such as "Tống ..." out."""
    return (
        bool(_TOTAL_LABEL.match(plain(cells[0])))
        and len(cells) < header_width
        and any(_number(c) is not None for c in cells[1:])
    )


def _trailing_total(rows: list[list[str | None]], roles: list[RowRole], header_width: int) -> None:
    """The table's last row, when it holds only a few numbers under blank leading cells after
    at least two records, is its total even when the sum cannot be checked (the records may be
    written in many formats)."""
    last = next(
        (i for i in range(len(roles) - 1, -1, -1) if roles[i] not in ("blank", "footer")), None
    )
    if last is None or roles[last] != "data" or rows[last][0] is not None:
        return
    cells = _filled(rows[last])
    if not cells or len(cells) > max(2, header_width // 2):
        return
    if all(_number(c) is not None for c in cells) and sum(r == "data" for r in roles) > 2:
        roles[last] = "total"


class _RunningSums:
    """Column sums of the records since the last total or section, kept as rows go by, so
    finding totals stays linear in the rows."""

    def __init__(self) -> None:
        self.records = 0
        self.sums: dict[int, float] = {}
        self.not_numbers: set[int] = set()

    def add(self, row: list[str | None]) -> None:
        self.records += 1
        for col, cell in enumerate(row):
            if cell is None:
                continue
            value = _number(cell)
            if value is None:
                self.not_numbers.add(col)
            else:
                self.sums[col] = self.sums.get(col, 0.0) + value

    def matches(self, row: list[str | None]) -> bool:
        """A sparse row whose numbers equal the column sums of at least two records above it."""
        cells = _filled(row)
        if self.records < 2 or not cells or len(cells) > max(2, len(row) // 2):
            return False
        found = False
        first = next(i for i, c in enumerate(row) if c is not None)
        for col, cell in enumerate(row):
            if cell is None:
                continue
            value = _number(cell)
            if value is None:
                # Only a leading label may sit beside the sums.
                if col != first or classify(cell) not in _TEXT_KINDS:
                    return False
                continue
            if value == 0 or col not in self.sums or col in self.not_numbers:
                return False
            if abs(self.sums[col] - value) >= 0.5:
                return False
            found = True
        return found
