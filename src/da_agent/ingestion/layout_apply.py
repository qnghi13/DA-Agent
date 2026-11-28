"""Apply one family layout to one sheet's grid: tidy rows, plus coverage of every filled cell.

Order: region, header layers, row roles (titles, totals, footers, repeated headers excluded),
sections, reshape, context. Coverage asks where each filled cell went: a header, a context or
section value, a record, or an excluded row. A cell nobody took is `left`; a cell taken twice
counts against the layout.
"""

from collections import Counter
from dataclasses import dataclass, field

from da_agent.ingestion.grid import Grid, Region, RowRole, find_regions, plain, row_roles
from da_agent.ingestion.layout_spec import (
    LAYER_JOIN,
    FamilyLayout,
    HeaderLayer,
    PairRows,
    RecordBlocks,
    Reshape,
    SplitCell,
    SplitGroups,
    Unpivot,
)
from da_agent.ingestion.profile import classify

MAX_LEFT = 20
Cell = tuple[int, int]


@dataclass
class TidyRow:
    cells: list[str | None]
    ordinal: int
    cell_range: str


@dataclass
class Coverage:
    filled: int
    used: int
    excluded: int
    twice: int
    left: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        if not self.filled:
            return 1.0
        return max(0.0, (self.used + self.excluded - self.twice) / self.filled)


@dataclass
class Applied:
    header: list[str]
    rows: list[TidyRow]
    coverage: Coverage
    problems: list[str] = field(default_factory=list)


@dataclass
class _Work:
    """Cell bookkeeping while a layout is applied."""

    grid: Grid
    uses: Counter[Cell] = field(default_factory=Counter)
    excluded: set[Cell] = field(default_factory=set)

    def use(self, row: int, col: int) -> None:
        if self.grid.value(row, col) is not None:
            self.uses[(row, col)] += 1

    def use_row(self, row: int, region: Region) -> None:
        for col in range(region.left, region.right + 1):
            self.use(row, col)

    def exclude_row(self, row: int, region: Region) -> None:
        for col in range(region.left, region.right + 1):
            if self.grid.value(row, col) is not None:
                self.excluded.add((row, col))

    def coverage(self) -> Coverage:
        filled = [
            (r, c)
            for r, row in enumerate(self.grid.cells)
            for c, value in enumerate(row)
            if value is not None and value.strip()
        ]
        used = [cell for cell in filled if self.uses[cell]]
        excluded = [cell for cell in filled if not self.uses[cell] and cell in self.excluded]
        left = [cell for cell in filled if not self.uses[cell] and cell not in self.excluded]
        return Coverage(
            filled=len(filled),
            used=len(used),
            excluded=len(excluded),
            twice=sum(1 for cell in used if self.uses[cell] > 1),
            left=[self.grid.address(r, c) for r, c in left[:MAX_LEFT]],
        )


def apply_sheet(grid: Grid, layouts: list[FamilyLayout]) -> tuple[list[Applied], Coverage]:
    """Apply every entry of a family (one per region) to one sheet, with one shared coverage."""
    work = _Work(grid)
    regions = find_regions(grid)
    applied = [apply_family(grid, layout, work, regions) for layout in layouts]
    return applied, work.coverage()


def apply_family(
    grid: Grid,
    layout: FamilyLayout,
    work: "_Work | None" = None,
    regions: list[Region] | None = None,
) -> Applied:
    work = work or _Work(grid)
    regions = regions if regions is not None else find_regions(grid)
    if layout.region > len(regions):
        return Applied([], [], work.coverage(), [f"region {layout.region} not found"])
    region = regions[layout.region - 1]
    roles = row_roles(grid, region)

    layers = _header_rows(layout.header, roles)
    if layout.header:
        spreads = [layer.spread == "right" for layer in layout.header][: len(layers)]
    else:  # code's layers: each one above the last labels a group of columns
        spreads = [k < len(layers) - 1 for k in range(len(layers))]
    names, parts = _header_names(grid, region, layers, spreads)
    for i in layers:
        work.use_row(region.top + i, region)
    first_header = layers[0] if layers else 0
    header_row = grid.row(region.top + layers[-1], region) if layers else None

    table = _Table(names=list(names), parts=parts)
    section: str | None = None
    excluded_kinds = set(layout.exclude)
    for i, role in enumerate(roles):
        row = region.top + i
        if i in layers or role == "blank":
            continue
        cells = grid.row(row, region)
        if i < first_header:  # above the header: a title
            if "title" in excluded_kinds:
                work.exclude_row(row, region)
        elif header_row is not None and _same(cells, header_row):
            work.exclude_row(row, region)  # a header repeated after a page break
        elif role == "footer" or (role in ("total", "title") and role in excluded_kinds):
            # Page markers and closing notes are never records, whatever the layout keeps.
            work.exclude_row(row, region)
        elif role == "section" and layout.sections:
            section = _first(cells)
            work.use_row(row, region)
        else:
            work.use_row(row, region)
            extra = [section] if layout.sections else []
            table.records.append(_Record([*cells, *extra], [row], region.left, region.right))
    if layout.sections:
        table.names.append(layout.sections)

    problems: list[str] = []
    for step in (layout.reshape, layout.then):
        if step is not None:
            problem = _reshape(table, step)
            if problem:
                problems.append(problem)

    context_names, context_values = _context(work, grid, layout)
    rows = [
        TidyRow([*record.cells, *context_values], ordinal, record.cell_range(grid))
        for ordinal, record in enumerate(table.records, start=1)
    ]
    return Applied([*table.names, *context_names], rows, work.coverage(), problems)


@dataclass
class _Record:
    cells: list[str | None]
    rows: list[int]
    left: int
    right: int

    def cell_range(self, grid: Grid) -> str:
        start = grid.address(min(self.rows), self.left)
        end = grid.address(max(self.rows), self.right)
        return start if start == end else f"{start}:{end}"


@dataclass
class _Table:
    names: list[str]
    # Per header column, its text in each header layer (None where the layer is blank there).
    parts: list[list[str | None]]
    records: list[_Record] = field(default_factory=list)
    # After pair_rows: each varying column's name -> the columns holding its value per label.
    pairs: dict[str, list[int]] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    source_columns: dict[str, int] = field(default_factory=dict)


def _same(cells: list[str | None], header: list[str | None]) -> bool:
    return [plain(c) if c else "" for c in cells] == [plain(c) if c else "" for c in header]


def _find(names: list[str], wanted: str) -> int | None:
    target = plain(wanted)
    for i, name in enumerate(names):
        if plain(name) == target:
            return i
    for i, name in enumerate(names):
        if plain(name.split(LAYER_JOIN)[-1]) == target:
            return i
    return None


def _reshape(table: _Table, step: Reshape) -> str | None:
    if isinstance(step, PairRows):
        return _pair_rows(table, step)
    if isinstance(step, SplitGroups):
        return _split_groups(table, step)
    if isinstance(step, Unpivot):
        return _unpivot(table, step)
    return _reshape_lines(table, step)


def _pair_rows(table: _Table, step: PairRows) -> str | None:
    column = _find(table.names, step.column)
    if column is None:
        return f"pair_rows: no column {step.column!r}"
    labels = [plain(v) for v in step.values]
    groups: list[dict[int, _Record]] = []
    for record in table.records:
        value = record.cells[column]
        position = labels.index(plain(value)) if value and plain(value) in labels else 0
        if position == 0 or not groups or position in groups[-1]:
            groups.append({position: record})
        else:
            groups[-1][position] = record
    others = [c for c in range(len(table.names)) if c != column]
    # A column varies when a later row of a group holds a value of its own; one that only repeats
    # the first row's value (a section filled down) is a key.
    varying = [
        c
        for c in others
        if any(
            g[p].cells[c] is not None and g[p].cells[c] != g[min(g)].cells[c]
            for g in groups
            for p in g
            if p != min(g)
        )
    ]
    keys = [c for c in others if c not in varying]
    names = [table.names[c] for c in keys]
    pairs: dict[str, list[int]] = {}
    for c in varying:
        pairs[table.names[c]] = list(range(len(names), len(names) + len(labels)))
        table.source_columns[table.names[c]] = c
        names += [f"{table.names[c]}{LAYER_JOIN}{label}" for label in step.values]
    records: list[_Record] = []
    for group in groups:
        first = group[min(group)]
        cells = [first.cells[c] for c in keys]
        for c in varying:
            cells += [group[p].cells[c] if p in group else None for p in range(len(labels))]
        rows = sorted(r for record in group.values() for r in record.rows)
        records.append(_Record(cells, rows, first.left, first.right))
    table.names, table.records, table.pairs, table.labels = names, records, pairs, step.values
    table.parts = []
    return None


def _split_groups(table: _Table, step: SplitGroups) -> str | None:
    layer = step.layer - 1
    # A group label covers its column and the blank ones right of it, whatever the layer's spread.
    group_of: list[str | None] = []
    carried: str | None = None
    for c in range(len(table.names)):
        pieces = table.parts[c] if c < len(table.parts) else []
        label = pieces[layer] if layer < len(pieces) else None
        if c < len(table.parts):
            carried = label if label is not None else carried
            label = carried
        group_of.append(label)
    if all(g is None for g in group_of):
        return f"split_groups: header layer {step.layer} has no group labels"
    layers = max((len(p) for p in table.parts), default=0)
    if layer >= layers - 1:
        return (
            f"split_groups: header layer {step.layer} holds the column names themselves; give "
            "the header rows with the group labels in a layer above the column names"
        )
    keys = [c for c, g in enumerate(group_of) if g is None]
    groups = list(dict.fromkeys(g for g in group_of if g is not None))
    sub_names: list[str] = []
    sub_of: dict[int, str] = {}
    for c, g in enumerate(group_of):
        if g is None:
            continue
        rest = [p for k, p in enumerate(table.parts[c]) if k != layer and p is not None]
        sub = LAYER_JOIN.join(rest) or g
        sub_of[c] = sub
        if sub not in sub_names:
            sub_names.append(sub)
    records: list[_Record] = []
    for record in table.records:
        for g in groups:
            columns = [c for c, label in enumerate(group_of) if label == g]
            values = {sub_of[c]: record.cells[c] for c in columns}
            if all(v is None for v in values.values()):
                continue
            cells = [record.cells[c] for c in keys] + [g] + [values.get(n) for n in sub_names]
            left = record.left + min(columns)
            right = record.left + max(columns)
            records.append(_Record(cells, record.rows, left, right))
    table.names = [table.names[c] for c in keys] + [step.name] + sub_names
    table.records, table.parts = records, []
    return None


def _unpivot(table: _Table, step: Unpivot) -> str | None:
    if table.pairs:
        bases = list(table.pairs)
        first, last = _find(bases, step.first), _find(bases, step.last)
        if first is not None and last is None:
            last = _like_run_end(bases, first)
        if first is None or last is None:
            return f"unpivot: no columns {step.first!r} to {step.last!r}"
        chosen = bases[first : last + 1]
        spread = {i for name in chosen for i in table.pairs[name]}
        keys = [c for c in range(len(table.names)) if c not in spread]
        records: list[_Record] = []
        for record in table.records:
            for name in chosen:
                values = [record.cells[i] for i in table.pairs[name]]
                if all(v is None for v in values):
                    continue
                column = record.left + table.source_columns.get(name, 0)
                cells = [record.cells[c] for c in keys] + [name, *values]
                records.append(_Record(cells, record.rows, column, column))
        table.names = [table.names[c] for c in keys] + [step.key, *table.labels]
        table.records, table.pairs = records, {}
        return None
    first, last = _find(table.names, step.first), _find(table.names, step.last)
    if first is not None and last is None:
        last = _like_run_end(table.names, first)
    if first is None or last is None or last < first:
        return f"unpivot: no columns {step.first!r} to {step.last!r}"
    spread = range(first, last + 1)
    keys = [c for c in range(len(table.names)) if c not in spread]
    records = []
    for record in table.records:
        for c in spread:
            value = record.cells[c]
            if value is None:
                continue
            cells = [record.cells[k] for k in keys] + [_short(table.names[c]), value]
            records.append(_Record(cells, record.rows, record.left + c, record.left + c))
    table.names = [table.names[c] for c in keys] + [step.key, step.value]
    table.records, table.parts = records, []
    return None


def _like_run_end(names: list[str], first: int) -> int:
    """The last of the columns from `first` whose names look alike (day numbers 1, 2, ... 30),
    for a sheet whose month is shorter than the one the layout was written from."""
    kind = classify(_short(names[first]))
    end = first
    while end + 1 < len(names) and classify(_short(names[end + 1])) == kind:
        end += 1
    return end


def _short(name: str) -> str:
    return name.split(LAYER_JOIN)[-1]


TEXT_COLUMN = "text"


def _is_separator_line(line: str) -> bool:
    return not any(ch.isalnum() for ch in line)


def _key_value(line: str, separator: str) -> tuple[str, str] | None:
    key, found, value = line.partition(separator)
    if not found or not key.strip():
        return None
    return key.strip(), value.strip()


def _ordered(keys: list[str], seen: list[str]) -> None:
    for key in keys:
        if key not in seen:
            seen.append(key)


def _reshape_lines(table: _Table, step: Reshape) -> str | None:
    if isinstance(step, RecordBlocks):
        return _record_blocks(table, step)
    if isinstance(step, SplitCell):
        return _split_cell(table, step)
    return f"{step.kind}: not supported"


def _record_blocks(table: _Table, step: RecordBlocks) -> str | None:
    """`key: value` lines down the first column. A separator line closes a group; a line whose key
    is `starts_with` opens a record; lines of a group before its first record are shared by every
    record of the group; a line with no key goes to a `text` column; a heading before any record
    is dropped."""
    start = plain(step.starts_with)
    keys: list[str] = []
    built: list[tuple[dict[str, str], list[int], int]] = []
    shared: dict[str, str] = {}
    shared_rows: list[int] = []
    current: dict[str, str] | None = None
    current_rows: list[int] = []
    repeated: dict[str, int] = {}
    left = table.records[0].left if table.records else 0

    def close() -> None:
        nonlocal current, current_rows
        if current is not None:
            built.append((current, sorted(current_rows), left))
        current, current_rows = None, []

    for record in table.records:
        line = next((c for c in record.cells if c is not None), None)
        if line is None:
            continue
        if _is_separator_line(line):
            close()
            shared, shared_rows = {}, []
            continue
        pair = _key_value(line, step.separator)
        if pair and plain(pair[0]) == start:
            close()
            current, current_rows = dict(shared), [*shared_rows]
        target = current if current is not None else shared
        if pair is None:
            if current is None:
                continue  # a heading before any record
            key, value = TEXT_COLUMN, line.strip()
            if key in target:
                value = f"{target[key]}\n{value}"
        else:
            key, value = pair
            if key in target and current is not None and key != TEXT_COLUMN:
                repeated[key] = repeated.get(key, 0) + 1
                value = target[key] if target[key] else value
        target[key] = value
        _ordered([key], keys)
        (current_rows if current is not None else shared_rows).extend(record.rows)
    close()
    if not built:
        found: dict[str, int] = {}
        # Every cell, line, and "|" part: keys packed in a calendar cell are listed too.
        for record in table.records:
            for cell in record.cells:
                for line in (cell or "").splitlines():
                    for part in line.split("|"):
                        pair = _key_value(part, step.separator)
                        if pair:
                            found[pair[0]] = found.get(pair[0], 0) + 1
        common = sorted(found, key=lambda k: -found[k])[:8]
        return (
            f"record_blocks: no line has the key {step.starts_with!r}; keys in the lines, most "
            f"common first: {', '.join(common) or 'none'}"
        )
    columns = [k for k in keys if k != TEXT_COLUMN] + ([TEXT_COLUMN] if TEXT_COLUMN in keys else [])
    table.names = columns
    table.records = [
        _Record([values.get(k) for k in columns], rows, col, col) for values, rows, col in built
    ]
    table.parts = []
    if repeated:
        # Only the first value of a repeated key is kept: the record holds several items.
        keys_text = ", ".join(f"{k!r} ({n} times)" for k, n in list(repeated.items())[:3])
        return (
            f"record_blocks: keys repeat inside records opened by {step.starts_with!r}: "
            f"{keys_text}; their later values are lost. Open records at the key that repeats"
        )
    return None


def _split_cell(table: _Table, step: SplitCell) -> str | None:
    """Cells holding `key: value | key: value` lines: one row per line, with the row's other
    (label) columns and the packed cell's column header."""
    packed = [
        c
        for c in range(len(table.names))
        if any(step.separator in (r.cells[c] or "") for r in table.records)
    ]
    if not packed:
        return f"split_cell: no cell holds {step.separator!r}"
    labels = [c for c in range(len(table.names)) if c not in packed]
    keys: list[str] = []
    lines_out: list[tuple[_Record, int, dict[str, str]]] = []
    for record in table.records:
        for c in packed:
            cell = record.cells[c]
            if not cell:
                continue
            for line in cell.splitlines():
                if not line.strip():
                    continue
                values: dict[str, str] = {}
                for part in line.split(step.field):
                    pair = _key_value(part, step.separator)
                    if pair:
                        values[pair[0]] = pair[1]
                    elif part.strip():
                        joined = values.get(TEXT_COLUMN)
                        values[TEXT_COLUMN] = f"{joined} {part.strip()}" if joined else part.strip()
                _ordered(list(values), keys)
                lines_out.append((record, c, values))
    columns = [k for k in keys if k != TEXT_COLUMN] + [TEXT_COLUMN]
    records = []
    for record, c, values in lines_out:
        cells = [record.cells[k] for k in labels] + [_short(table.names[c])]
        cells += [values.get(k) for k in columns]
        records.append(_Record(cells, record.rows, record.left + c, record.left + c))
    table.names = [table.names[k] for k in labels] + [step.name, *columns]
    table.records, table.parts = records, []
    return None


def _first(cells: list[str | None]) -> str | None:
    return next((c.strip() for c in cells if c is not None), None)


def _header_rows(layers: list[HeaderLayer], roles: list[RowRole]) -> list[int]:
    """0-based rows within the region: the layout's, or else code's first header block."""
    if layers:
        return [layer.row - 1 for layer in layers if layer.row - 1 < len(roles)]
    found: list[int] = []
    for i, role in enumerate(roles):
        if role == "header" and (not found or i == found[-1] + 1):
            found.append(i)
        elif found:
            break
    return found


def _header_names(
    grid: Grid, region: Region, layers: list[int], spreads: list[bool]
) -> tuple[list[str], list[list[str | None]]]:
    """Column names joined from the header layers, and each column's text per layer."""
    width = region.right - region.left + 1
    parts: list[list[str | None]] = [[] for _ in range(width)]
    for k, i in enumerate(layers):
        carried: str | None = None
        for offset in range(width):
            value = grid.value(region.top + i, region.left + offset)
            if value is not None:
                carried = value.strip()
                parts[offset].append(carried)
            elif spreads[k] and carried is not None:
                parts[offset].append(carried)
            else:
                parts[offset].append(None)
    names: list[str] = []
    seen: Counter[str] = Counter()
    for offset, pieces in enumerate(parts):
        filled = [p for p in pieces if p is not None]
        name = LAYER_JOIN.join(filled) if filled else f"column_{offset + 1}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names, parts


def _context(work: _Work, grid: Grid, layout: FamilyLayout) -> tuple[list[str], list[str | None]]:
    names: list[str] = []
    values: list[str | None] = []
    file_name, _, sheet_name = grid.source.partition("#")
    for item in layout.context:
        value: str | None = None
        if item.source == "sheet_name":
            value = sheet_name or None
        elif item.source == "file_name":
            value = file_name
        elif item.source == "cell" and item.cell:
            cell = _parse_cell(grid, item.cell)
            if cell is not None:
                value = grid.value(*cell)
                work.use(*cell)
        elif item.source == "after_label" and item.label:
            value = _after_label(work, grid, item.label)
        names.append(item.name)
        values.append(value.strip() if value else None)
    return names, values


def _parse_cell(grid: Grid, address: str) -> Cell | None:
    letters = "".join(ch for ch in address if ch.isalpha())
    digits = "".join(ch for ch in address if ch.isdigit())
    if not letters or not digits:
        return None
    col = 0
    for ch in letters.upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    row = int(digits) - grid.first_row
    return (row, col - 1) if row >= 0 else None


def _after_label(work: _Work, grid: Grid, label: str) -> str | None:
    """The first filled cell right of the first cell reading `label` (or `label:`)."""
    wanted = plain(label).rstrip(":").strip()
    for r, row in enumerate(grid.cells):
        for c, value in enumerate(row):
            if value is None or plain(value).rstrip(":").strip() != wanted:
                continue
            for right in range(c + 1, len(row)):
                found = grid.value(r, right)
                if found is not None:
                    work.use(r, c)
                    work.use(r, right)
                    return found
    return None
