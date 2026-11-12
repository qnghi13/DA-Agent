"""Memory as documents for the model (ADR 0030).

Approved notes from the people who use a dataset (corrections, aliases, verified examples) and the
earlier turns of a conversation reach the model as documents, so they share the documents' frame:
data, never instructions. Notes sit in the prompt while they are few; past that, the prompt lists
their titles and search_rules fetches them, as for on-demand rules (ADR 0008).

The worker loads them from storage; eval runs pass none.
"""

from dataclasses import dataclass

from da_agent.agent.loop_types import Document

# Notes beyond this many are fetched on demand instead of sitting in every prompt.
INLINE_NOTES = 10
TITLE_CHARS = 60

NOTES_NAME = "Notes approved by the people who use this dataset"
NOTES_PREFACE = (
    "Corrections, aliases, and verified examples that people approved for this dataset. Where a "
    "correction disagrees with the other documents, the correction wins. Examples show a query "
    "that once answered a question; check it against the data before reusing it."
)
TURNS_NAME = "Earlier turns of this conversation"
TURNS_PREFACE = (
    "The questions asked before in this conversation and what was answered. Use them only to "
    "understand what the new question refers to, such as 'that month' or 'the same product'. "
    "Compute every number again with a query."
)

_KIND_ORDER = {"correction": 0, "alias": 1, "example": 2}
_KIND_LABEL = {"correction": "Correction", "alias": "Alias", "example": "Example"}


@dataclass(frozen=True)
class MemoryNote:
    kind: str
    text: str
    sql: str | None
    author: str


@dataclass(frozen=True)
class EarlierTurn:
    question: str
    answer: str
    sql: str | None


def _indented(text: str) -> str:
    """Note text is indented, so a line in it cannot start a section heading of its own."""
    return "\n".join(f"  {line}" for line in text.strip().splitlines())


def _title(number: int, note: MemoryNote) -> str:
    first = " ".join(note.text.split())
    if len(first) > TITLE_CHARS:
        first = first[: TITLE_CHARS - 1] + "…"
    return f"{_KIND_LABEL.get(note.kind, 'Note')} {number}: {first}"


def _note_section(number: int, note: MemoryNote) -> str:
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
        documents.append(
            Document(
                name=NOTES_NAME,
                text="\n\n".join([NOTES_PREFACE, *sections]),
                on_demand=len(notes) > INLINE_NOTES,
            )
        )
    if turns:
        texts = [_turn_text(n, turn) for n, turn in enumerate(turns, start=1)]
        documents.append(Document(name=TURNS_NAME, text="\n\n".join([TURNS_PREFACE, *texts])))
    return documents
