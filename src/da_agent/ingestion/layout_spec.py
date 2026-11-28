"""The layout spec: how to turn a family of sheets into tidy rows, as data that code interprets.

A model proposes it from code's findings (`FamilyFinding`). It holds no code and no patterns, only
choices from a fixed set of operators with bounded sizes (ADR 0006). `check_layout` drops each
family entry that names something the findings do not have, with a problem the model can fix.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from da_agent.ingestion.grid import plain
from da_agent.ingestion.report import FamilyFinding

NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,62}$"
Name = Annotated[str, Field(pattern=NAME_PATTERN)]
Label = Annotated[str, Field(min_length=1, max_length=100)]
Mark = Annotated[str, Field(min_length=1, max_length=5)]
_CELL = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")
_RANGE = re.compile(r"^([A-Z]{1,3})(\d{1,7}):([A-Z]{1,3})(\d{1,7})$")
LAYER_JOIN = " / "
RowKind = Literal["title", "total", "footer", "blank"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeaderLayer(_Model):
    row: int = Field(ge=1, le=10, description="1-based row within the region.")
    spread: Literal["none", "right"] = Field(
        "none", description='"right": a label also names the blank header cells to its right.'
    )


class ContextValue(_Model):
    name: Name
    source: Literal["sheet_name", "file_name", "cell", "after_label"]
    cell: str | None = Field(None, max_length=10, description='For source "cell", such as "B2".')
    label: Label | None = Field(
        None, description='For source "after_label": the first filled cell right of this label.'
    )


class Unpivot(_Model):
    kind: Literal["unpivot"]
    first: Label = Field(description="Header name of the first column to turn into rows.")
    last: Label = Field(description="Header name of the last column to turn into rows.")
    key: Name
    value: Name


class SplitGroups(_Model):
    kind: Literal["split_groups"]
    layer: int = Field(1, ge=1, le=3, description="The header layer whose labels name groups.")
    name: Name


class PairRows(_Model):
    kind: Literal["pair_rows"]
    column: Label
    values: list[Label] = Field(min_length=2, max_length=4)


class RecordBlocks(_Model):
    kind: Literal["record_blocks"]
    separator: Mark = ":"
    starts_with: Label = Field(description="The key whose line opens a record.")


class SplitCell(_Model):
    kind: Literal["split_cell"]
    name: Name = Field("column", description="New column holding each packed cell's header.")
    line: Literal["newline"] = "newline"
    field: Mark = "|"
    separator: Mark = ":"


Reshape = Annotated[
    Unpivot | SplitGroups | PairRows | RecordBlocks | SplitCell, Field(discriminator="kind")
]


class FamilyLayout(_Model):
    family: str = Field(max_length=10)
    region: int = Field(1, ge=1, le=20)
    header: list[HeaderLayer] = Field(default_factory=list, max_length=3)
    context: list[ContextValue] = Field(default_factory=list, max_length=8)
    sections: Name | None = None
    exclude: list[RowKind] = Field(
        default_factory=lambda: ["title", "total", "footer", "blank"], max_length=4
    )
    reshape: Reshape | None = None
    # A second step on the first one's output, such as paired rows and then unpivot of days.
    then: Reshape | None = None


class LayoutSpec(_Model):
    families: list[FamilyLayout] = Field(default_factory=list, max_length=100)


def range_rows(bounds: str) -> int | None:
    match = _RANGE.match(bounds)
    return int(match.group(4)) - int(match.group(2)) + 1 if match else None


def _known(name: str, columns: set[str]) -> bool:
    """A header text as is ("In/Out"), or layers joined by " / " ("TERM I / TX1")."""
    if plain(name) in columns:
        return True
    return all(plain(part) in columns for part in name.split(LAYER_JOIN.strip()) if part.strip())


def _family_problem(layout: FamilyLayout, finding: FamilyFinding) -> str | None:
    if layout.region > finding.regions:
        return f"region {layout.region} does not exist: the family has {finding.regions}"
    bounds = finding.region_bounds[layout.region - 1] if finding.region_bounds else ""
    height = range_rows(bounds)
    for layer in layout.header:
        if height is not None and layer.row > height:
            return f"header row {layer.row} is past region {layout.region} ({bounds})"
    columns = {plain(c) for c in finding.columns}
    names: list[str] = []
    for reshape in (layout.reshape, layout.then):
        if isinstance(reshape, Unpivot):
            names += [reshape.first, reshape.last]
        elif isinstance(reshape, PairRows):
            names.append(reshape.column)
    for name in names:
        if not _known(name, columns):
            return f"column {name!r} is not a header name of the family"
    for value in layout.context:
        if value.source == "cell" and (value.cell is None or not _CELL.match(value.cell)):
            return f"context {value.name!r} needs a cell address such as B2, not {value.cell!r}"
        if value.source == "after_label" and not value.label:
            return f"context {value.name!r} needs a label"
    return None


def check_layout(spec: LayoutSpec, findings: list[FamilyFinding]) -> tuple[LayoutSpec, list[str]]:
    """Keep the family entries that fit the findings; describe why each other one was dropped."""
    by_family = {f.family: f for f in findings}
    kept: list[FamilyLayout] = []
    problems: list[str] = []
    for layout in spec.families:
        finding = by_family.get(layout.family)
        if finding is None:
            known = ", ".join(by_family) or "none"
            problems.append(f"family {layout.family!r} does not exist (known: {known})")
            continue
        if any((k.family, k.region) == (layout.family, layout.region) for k in kept):
            problems.append(
                f"family {layout.family!r} region {layout.region} is described twice: "
                "the first is kept"
            )
            continue
        problem = _family_problem(layout, finding)
        if problem:
            problems.append(f"family {layout.family!r} dropped: {problem}")
            continue
        kept.append(layout)
    return LayoutSpec(families=kept), problems
