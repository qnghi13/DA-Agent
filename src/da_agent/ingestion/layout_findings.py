"""Sheet families and the flags that send them to the layout stage.

A family is a set of sheets or files that share one layout: the same number of header rows and
tables, and header names that match after case, accents, and order are ignored. A family is
flagged when its layout is not "one header row, then records"; only flagged families reach the
model. Each finding carries a bounded preview of the first sheet's grid, with sensitive values
masked, for the model to read.
"""

import hashlib
import re
from dataclasses import dataclass, field

from da_agent.ingestion.grid import (
    Grid,
    Region,
    RowRole,
    find_regions,
    number_run,
    plain,
    row_roles,
)
from da_agent.ingestion.profile import SENSITIVE_MARK, SENSITIVE_PATTERNS, classify, sensitive_name
from da_agent.ingestion.report import FamilyFinding

PREVIEW_CELLS = 4000
PREVIEW_CELL_CHARS = 80
ANCHOR_NEIGHBOURS = 2
FAMILY_SIMILARITY = 0.9
FAMILY_OVERLAP = 0.75
ONE_COLUMN_ROWS = 10
PACKED_CELLS = 3
# Rows come in pairs (or threes) when one column cycles through the same few values this often.
PAIR_SHARE = 0.8
PAIR_MIN_ROWS = 4
MAX_CONTEXT_CELLS = 12
MAX_LISTED_SOURCES = 5
# A layout names regions 1 to 20 (FamilyLayout.region), so findings list no more.
MAX_REGIONS = 20
_DEFAULT_SHEET = re.compile(r"^(sheet|trang tinh|page)\s*\d*$")
# Month and period sheet names ("T01.2025", "Thang 02-2025", "04.2025"): rows usually carry the
# date already, so such names do not by themselves send a family to the model.
_DATE_SHEET = re.compile(
    r"^((thang|t|ky|month|m|q|quy)[\s._-]*)?\d{1,2}([\s._/-]+\d{2,4})?$|^\d{4}([\s._/-]+\d{1,2})?$"
)


@dataclass
class SheetLayout:
    """What code found in one sheet. Small, so a whole upload's sheets fit in memory."""

    source: str
    regions: list[Region]
    tables: int
    header_rows: int
    header: list[str]
    columns: list[str]
    flags: set[str] = field(default_factory=set)
    context_cells: list[str] = field(default_factory=list)
    # Title and side-block cell texts by address: context when they differ across a family.
    context_values: dict[str, str] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    bounds: list[str] = field(default_factory=list)
    preview: str = ""


def _normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", plain(name)).strip("_")


def _area(region: Region) -> int:
    return region.height * (region.right - region.left + 1)


def _is_table(region: Region) -> bool:
    return region.height >= 2 and region.right > region.left


def describe_sheet(
    grid: Grid, preview_cells: int = PREVIEW_CELLS, fast_header_row: int | None = None
) -> SheetLayout:
    """`fast_header_row`: the sheet row the fast path takes as the header, 0 when it found none,
    None when unknown."""
    regions = find_regions(grid)
    if not regions:
        return SheetLayout(grid.source, [], 0, 0, [], [])
    main = max(regions, key=_area)
    roles = {region: row_roles(grid, region) for region in regions}
    main_roles = roles[main]
    header_at = [i for i, role in enumerate(main_roles) if role == "header"]
    block = _first_block(header_at)
    header_cells = [grid.row(main.top + i, main) for i in block]
    header = [c for c in (header_cells[-1] if header_cells else []) if c is not None]
    columns = list(dict.fromkeys(c.strip() for row in header_cells for c in row if c is not None))
    tables = sum(_is_table(r) for r in regions)
    flags: set[str] = set()
    if tables > 1:
        flags.add("regions")
    if len(block) > 1:
        flags.add("header_rows")
    if header_cells and number_run(header_cells[-1]):
        flags.add("numeric_header")
    if "section" in main_roles:
        flags.add("sections")
    if main.right == main.left and main.height >= ONE_COLUMN_ROWS:
        flags.add("one_column")
    if _packed(grid):
        flags.add("packed_cells")
    hints: list[str] = []
    paired = _paired_rows(grid, main, main_roles, header_cells)
    if paired:
        flags.add("paired_rows")
        hints.append(paired)
    if block and any(i > block[-1] + 1 and main_roles[i - 1] != "header" for i in header_at):
        flags.add("repeated_header")
    context = _context_cells(grid, regions, roles, main)
    header_sheet_rows = [grid.first_row + main.top + i for i in block]
    if (
        fast_header_row is not None
        and header_sheet_rows
        and fast_header_row not in header_sheet_rows
    ):
        flags.add("header_moved")
    layout = SheetLayout(
        source=grid.source,
        regions=regions,
        tables=tables,
        header_rows=len(block),
        header=header,
        columns=columns,
        flags=flags,
        context_cells=context,
        hints=hints,
        context_values={c: grid.value(*_cell_position(grid, c)) or "" for c in context},
        bounds=[
            f"{grid.address(r.top, r.left)}:{grid.address(r.bottom, r.right)}"
            for r in regions[:MAX_REGIONS]
        ],
    )
    # Always built: a family of clean sheets is still flagged when their headers drift.
    layout.preview = _preview(grid, regions, roles, preview_cells)
    return layout


def _cell_position(grid: Grid, label: str) -> tuple[int, int]:
    address = label.removeprefix("cell ")
    letters = "".join(ch for ch in address if ch.isalpha())
    col = 0
    for ch in letters:
        col = col * 26 + ord(ch) - ord("A") + 1
    return int(address[len(letters) :]) - grid.first_row, col - 1


def _first_block(header_at: list[int]) -> list[int]:
    block: list[int] = []
    for i in header_at:
        if block and i != block[-1] + 1:
            break
        block.append(i)
    return block


def _paired_rows(
    grid: Grid, region: Region, roles: list[RowRole], header_cells: list[list[str | None]]
) -> str | None:
    """A column whose values cycle through the same 2 to 4 labels row after row (In, Out), with
    the first column blank on every row but the first of each cycle."""
    rows = [grid.row(region.top + i, region) for i, role in enumerate(roles) if role == "data"]
    if len(rows) < PAIR_MIN_ROWS or not header_cells:
        return None
    names = header_cells[-1]
    for col in range(1, region.right - region.left + 1):
        values = [plain(c) if (c := r[col]) else "" for r in rows]
        labels = list(dict.fromkeys(v for v in values if v))
        if not 2 <= len(labels) <= 4:
            continue
        in_cycle = sum(v == labels[k % len(labels)] for k, v in enumerate(values))
        later = [r for r, v in zip(rows, values, strict=True) if v and v != labels[0]]
        blank_first = sum(r[0] is None for r in later)
        if in_cycle >= PAIR_SHARE * len(rows) and later and blank_first >= PAIR_SHARE * len(later):
            name = names[col] if col < len(names) and names[col] else f"column {col + 1}"
            texts = [c for r in rows if (c := r[col])]
            shown = [next(t.strip() for t in texts if plain(t) == lab) for lab in labels]
            return (
                f"rows come in groups by column {name!r}: {', '.join(shown)}; "
                "the later rows of a group leave the first columns blank"
            )
    return None


def _packed(grid: Grid) -> bool:
    """Cells holding several lines, most of them `key: value`."""
    count = 0
    for row in grid.cells:
        for cell in row:
            if cell and "\n" in cell:
                lines = [line for line in cell.splitlines() if line.strip()]
                if len(lines) >= 2 and sum(":" in line for line in lines) * 2 > len(lines):
                    count += 1
                    if count >= PACKED_CELLS:
                        return True
    return False


def _context_cells(
    grid: Grid, regions: list[Region], roles: dict[Region, list[RowRole]], main: Region
) -> list[str]:
    """Addresses of title cells and of cells in small blocks beside the tables."""
    cells: list[str] = []
    for region in regions:
        small = not _is_table(region)
        for i, role in enumerate(roles[region]):
            if role != "title" and not (small and role != "blank"):
                continue
            row = region.top + i
            for col in range(region.left, region.right + 1):
                if grid.value(row, col) is not None:
                    cells.append(f"cell {grid.address(row, col)}")
    return cells[:MAX_CONTEXT_CELLS]


def _masked_columns(grid: Grid, region: Region, roles: list[RowRole]) -> set[int]:
    masked: set[int] = set()
    for i, role in enumerate(roles):
        if role == "header":
            for col in range(region.left, region.right + 1):
                name = grid.value(region.top + i, col)
                if name and sensitive_name(name):
                    masked.add(col)
    return masked


def _sensitive_value(value: str) -> bool:
    """A phone number, ID, or email, also when written with spaces, dots, or dashes."""
    compact = re.sub(r"[\s.\-()]", "", value)
    return classify(value) in SENSITIVE_PATTERNS or classify(compact) in SENSITIVE_PATTERNS


def _mask_parts(text: str) -> str:
    """Mask each `key: value` part whose key names personal data or whose value is personal."""
    lines = []
    for line in text.split("\n"):
        parts = []
        for part in line.split("|"):
            key, found, value = part.partition(":")
            if found and key.strip() and (sensitive_name(key) or _sensitive_value(value.strip())):
                part = f"{key}: {SENSITIVE_MARK}"
            parts.append(part)
        lines.append("|".join(parts))
    return "\n".join(lines)


def _show(value: str | None, masked: bool) -> str:
    if value is None:
        return ""
    if masked or _sensitive_value(value):
        return SENSITIVE_MARK
    text = _mask_parts(value) if ":" in value else value
    text = text.replace("\r", "").replace("\n", "⏎")
    return text[:PREVIEW_CELL_CHARS]


def _preview(
    grid: Grid, regions: list[Region], roles: dict[Region, list[RowRole]], budget: int
) -> str:
    lines: list[str] = []
    listed = regions[:MAX_REGIONS]
    budget -= len(listed)  # each summary line costs a cell, so many regions cannot bloat it
    for number, region in enumerate(listed, start=1):
        counts: dict[str, int] = {}
        for role in roles[region]:
            counts[role] = counts.get(role, 0) + 1
        summary = ", ".join(f"{role} {n}" for role, n in counts.items() if role != "blank")
        start, end = (
            grid.address(region.top, region.left),
            grid.address(region.bottom, region.right),
        )
        lines.append(f"region {number}: {start}:{end} ({summary})")
    if len(regions) > len(listed):
        lines.append(f"... {len(regions) - len(listed)} more regions not shown")
    for region in listed:
        region_roles = roles[region]
        masked = _masked_columns(grid, region, region_roles)
        shown = _anchor_rows(region_roles)
        previous = None
        for i in shown:
            if region_roles[i] == "blank":
                continue
            cells = [
                _show(grid.value(region.top + i, col), col in masked)
                for col in range(region.left, region.right + 1)
            ]
            while cells and cells[-1] == "":
                cells.pop()
            if budget < len(cells):
                lines.append("... preview cut")
                return "\n".join(lines)
            budget -= len(cells)
            if previous is not None and i > previous + 1:
                lines.append(f"... {i - previous - 1} rows")
            address = grid.address(region.top + i, region.left)
            lines.append(f"{address}: {' | '.join(cells)}")
            previous = i
    return "\n".join(lines)


def _anchor_rows(roles: list[RowRole]) -> list[int]:
    """Rows where the role changes, the first and last rows, and their neighbours."""
    anchors = {0, len(roles) - 1}
    anchors |= {i for i in range(1, len(roles)) if roles[i] != roles[i - 1]}
    shown: set[int] = set()
    for i in anchors:
        shown.update(
            range(max(0, i - ANCHOR_NEIGHBOURS), min(len(roles), i + ANCHOR_NEIGHBOURS + 1))
        )
    return sorted(shown)


def _family_id(members: list[SheetLayout], taken: set[str]) -> str:
    """A short id from the header names every sheet of the family shares, its header rows, and its
    tables, so a layout written for it still finds it when files are added in any order."""
    first = members[0]
    shared = set.intersection(*({_normal(n) for n in m.header} for m in members))
    names = ",".join(sorted(shared or {_normal(n) for n in first.header}))
    signature = f"{first.header_rows}|{first.tables}|{names}"
    base = "f" + hashlib.sha1(signature.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    family, n = base, 1
    while family in taken:
        n += 1
        family = f"{base}-{n}"
    return family


def _similar(a: SheetLayout, b: SheetLayout) -> bool:
    if (a.header_rows, a.tables) != (b.header_rows, b.tables):
        return False
    left, right = {_normal(n) for n in a.header}, {_normal(n) for n in b.header}
    if not left and not right:
        return a.flags == b.flags
    # A sheet that adds a column or two to its family's header still belongs to the family.
    shared = len(left & right)
    return (
        shared / min(len(left), len(right)) >= FAMILY_SIMILARITY
        and shared / len(left | right) >= FAMILY_OVERLAP
    )


def _sheet(source: str) -> str:
    return source.split("#", 1)[1] if "#" in source else ""


def _file(source: str) -> str:
    return source.split("#", 1)[0]


def find_families(
    sheets: list[Grid | SheetLayout], preview_cells: int = PREVIEW_CELLS
) -> list[FamilyFinding]:
    layouts = [
        s if isinstance(s, SheetLayout) else describe_sheet(s, preview_cells) for s in sheets
    ]
    families: list[list[SheetLayout]] = []
    for layout in layouts:
        for family in families:
            if _similar(family[0], layout):
                family.append(layout)
                break
        else:
            families.append([layout])

    findings: list[FamilyFinding] = []
    for members in families:
        first = members[0]
        flags = set().union(*(m.flags for m in members))
        orders = {tuple(_normal(n) for n in m.header) for m in members}
        if len(orders) > 1:
            flags.add("drifting_header")
        addresses = list(dict.fromkeys(a for m in members for a in m.context_values))
        varying = [
            a.removeprefix("cell ")
            for a in addresses
            if len({m.context_values.get(a, "") for m in members}) > 1
        ]
        if len(members) > 1 and varying:
            flags.add("title_context")
        sheet_names = {plain(_sheet(m.source)) for m in members if "#" in m.source}
        if len(sheet_names) > 1 and not all(
            _DATE_SHEET.match(n) or _DEFAULT_SHEET.match(n) for n in sheet_names
        ):
            flags.add("sheet_context")
        if not flags:
            continue
        sources = [m.source for m in members]
        context: list[str] = []
        names = {_sheet(s) for s in sources}
        if len(names) > 1 or (
            len(names) == 1
            and any(ch.isdigit() for ch in next(iter(names)))
            and not _DEFAULT_SHEET.match(plain(next(iter(names))))
        ):
            context.append("sheet_name")
        if len({_file(s) for s in sources}) > 1:
            context.append("file_name")
        context += first.context_cells
        preview = first.preview
        listed = ", ".join(sources[1 : 1 + MAX_LISTED_SOURCES])
        more = len(sources) - 1 - MAX_LISTED_SOURCES
        header_line = f"first sheet: {first.source}"
        if listed:
            header_line += f"; also {listed}" + (f" and {more} more" if more > 0 else "")
        family = _family_id(members, {f.family for f in findings})
        findings.append(
            FamilyFinding(
                family=family,
                sources=sources,
                flags=sorted(flags),
                regions=len(first.regions),
                region_bounds=first.bounds,
                columns=first.columns,
                context_candidates=context,
                varying_cells=varying[:MAX_CONTEXT_CELLS],
                hints=first.hints,
                preview=f"{header_line}\n{preview}",
            )
        )
    return findings
