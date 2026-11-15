"""Memory as documents for the model (ADR 0030).

Approved notes from the people who use a dataset (corrections, aliases, verified examples) and the
earlier turns of a conversation reach the model as documents, so they share the documents' frame:
data, never instructions. Notes sit in the prompt while they are few; past that, the prompt lists
their titles and search_rules fetches them, as for on-demand rules (ADR 0008).

The worker loads them from storage; eval runs pass none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from da_agent.agent.loop_types import Document

if TYPE_CHECKING:
    from da_agent.reports.metric import Definition

# Notes beyond this many are fetched on demand instead of sitting in every prompt.
INLINE_NOTES = 10
TITLE_CHARS = 60

NOTES_NAME = "Notes approved by the people who use this dataset"
NOTES_PREFACE = (
    "Corrections, aliases, and verified examples that people approved for this dataset. Where a "
    "correction disagrees with the other documents, the correction wins. Examples show a query "
    "that once answered a question; check it against the data before reusing it."
)
METRICS_PREFACE = (
    " A metric is how the people who use this dataset compute that word; use it whenever the "
    "question asks for it."
)
TURNS_NAME = "Earlier turns of this conversation"
TURNS_PREFACE = (
    "The questions asked before in this conversation and what was answered. Use them only to "
    "understand what the new question refers to, such as 'that month' or 'the same product'. "
    "Compute every number again with a query."
)

_KIND_ORDER = {"metric": 0, "correction": 1, "alias": 2, "example": 3}
_KIND_LABEL = {
    "metric": "Metric",
    "correction": "Correction",
    "alias": "Alias",
    "example": "Example",
}


@dataclass(frozen=True)
class MemoryNote:
    kind: str
    text: str
    sql: str | None
    author: str
    # Set for metrics only.
    name: str | None = None
    aliases: tuple[str, ...] = ()
    definition: Definition | None = None


@dataclass(frozen=True)
class EarlierTurn:
    question: str
    answer: str
    sql: str | None


def _indented(text: str) -> str:
    """Note text is indented, so a line in it cannot start a section heading of its own."""
    return "\n".join(f"  {line}" for line in text.strip().splitlines())


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _metric_title(note: MemoryNote) -> str:
    name = _one_line(note.name or "")
    others = [_one_line(alias) for alias in note.aliases if alias.strip()]
    return f"{name} (also: {', '.join(others)})" if others else name


def _title(number: int, note: MemoryNote) -> str:
    if note.kind == "metric":
        return f"Metric {number}: {_metric_title(note)}"
    first = " ".join(note.text.split())
    if len(first) > TITLE_CHARS:
        first = first[: TITLE_CHARS - 1] + "…"
    return f"{_KIND_LABEL.get(note.kind, 'Note')} {number}: {first}"


def _metric_body(note: MemoryNote, definition: Definition) -> list[str]:
    name = _one_line(note.name or "")
    parts = [
        f"  When the question asks for {name}, compute it on table {definition.table} as:",
        "  " + _indented(definition.measure),
    ]
    if definition.filters:
        parts.append("  with these row filters, in addition to the question's period and grouping:")
        parts += ["  " + _indented(f) for f in definition.filters]
    else:
        parts.append("  with no row filters beyond the question's period and grouping.")
    return parts


def _note_section(number: int, note: MemoryNote) -> str:
    if note.kind == "metric" and note.definition is not None:
        parts = [f"## {_title(number, note)}", *_metric_body(note, note.definition)]
        parts.append(f"  (written by {' '.join(note.author.split())})")
        return "\n".join(parts)
    parts = [f"## {_title(number, note)}", _indented(note.text)]
    if note.sql:
        parts.append("  SQL:\n" + _indented(note.sql))
    parts.append(f"  (written by {' '.join(note.author.split())})")
    return "\n".join(parts)


def _turn_text(number: int, turn: EarlierTurn) -> str:
    parts = [f"Turn {number}", "  Question:", _indented(turn.question), "  Answer:"]
    parts.append(_indented(turn.answer))
    if turn.sql:
        parts.extend(["  Last query:", _indented(turn.sql)])
    return "\n".join(parts)


def memory_documents(notes: list[MemoryNote], turns: list[EarlierTurn]) -> list[Document]:
    """The documents to append after the dataset's own: approved notes, then earlier turns."""
    documents = []
    if notes:
        ordered = sorted(notes, key=lambda note: _KIND_ORDER.get(note.kind, len(_KIND_ORDER)))
        sections = [_note_section(n, note) for n, note in enumerate(ordered, start=1)]
        has_metrics = any(note.kind == "metric" for note in notes)
        preface = NOTES_PREFACE + (METRICS_PREFACE if has_metrics else "")
        documents.append(
            Document(
                name=NOTES_NAME,
                text="\n\n".join([preface, *sections]),
                on_demand=len(notes) > INLINE_NOTES,
            )
        )
    if turns:
        texts = [_turn_text(n, turn) for n, turn in enumerate(turns, start=1)]
        documents.append(Document(name=TURNS_NAME, text="\n\n".join([TURNS_PREFACE, *texts])))
    return documents
