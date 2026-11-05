"""Connects the generic agent to DataMate: shop files, documents, and the submission format."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from da_agent.agent.answer import AgentResult
from da_agent.agent.loop_types import Document
from da_agent.agent.rules import split_sections
from da_agent.storage.sqlite_dataset import ReferenceTable, SqliteDataset

KIND_TO_TYPE = {
    "number": "number",
    "percent": "percent",
    "date": "date",
    "boolean": "boolean",
    "text": "string",
    "list": "list",
    "table": "table",
}

# Personal data that the agent does not need. Replaced by DatasetSpec when ingestion exists.
HIDDEN_COLUMNS = frozenset(
    {
        "khach_hang.cccd",
        "khach_hang.email",
        "khach_hang.SoDienThoai",
        "khach_hang.ngay_sinh",
        "khach_hang.DiaChiLienHe",
    }
)


class UnsupportedShopError(ValueError):
    """The shop's files need ingestion, which does not exist yet."""


@dataclass(frozen=True)
class ShopContext:
    dataset: SqliteDataset
    documents: list[Document]


def to_submission(question_id: str, result: AgentResult) -> dict[str, Any]:
    answer = result.answer
    if answer is None:
        return {"id": question_id, "type": "abstain"}
    if answer.kind == "unanswerable":
        row: dict[str, Any] = {"id": question_id, "type": "unanswerable"}
        if answer.reason is not None:
            row["reason"] = answer.reason
        return row
    return {"id": question_id, "type": KIND_TO_TYPE[answer.kind], "answer": answer.value}


def _load_calendar(path: Path) -> ReferenceTable:
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    return ReferenceTable(columns=rows[0], rows=rows[1:])


# Lines about the submission file and grading. The agent never writes submission lines, because
# to_submission does, so these lines only cost tokens. Rules about values and rounding stay.
_SCORING_ONLY_PREFIXES = (
    "Mỗi dòng `submission.jsonl`",
    "Trường `reason` không bắt buộc",
)


def agent_rulebook(text: str) -> str:
    """The rulebook as the agent needs it, without submission and grading mechanics."""
    lines = text.splitlines()
    kept = [line for line in lines if not line.startswith(_SCORING_ONLY_PREFIXES)]
    return "\n".join(kept)


# Sections that shape every answer stay in the prompt: the preface (what "today" means),
# unanswerable questions, file conventions, answer format, and glossary. Topic sections such as
# revenue, time, debt, and stock are fetched with search_rules when a question needs them.
_INLINE_SECTION_NUMBERS = {"7", "8", "9", "10"}


def rulebook_documents(text: str) -> tuple[Document, Document]:
    """Split the rulebook into an inline document and an on-demand document of topic sections."""
    preface, sections = split_sections(text)
    inline, topics = [preface], []
    for section in sections:
        number = section.title.split(".", 1)[0].strip()
        (inline if number in _INLINE_SECTION_NUMBERS else topics).append(section.text)
    return (
        Document("rulebook.md", "\n\n".join(inline)),
        Document("rulebook-topics.md", "\n\n".join(topics), on_demand=True),
    )


def open_shop(data_dir: Path, shop: str) -> ShopContext:
    """Open one shop. This reads only the rulebook, the calendar, and that shop's directory."""
    directory = data_dir / "data" / shop
    database = directory / "pos.sqlite"
    if not database.is_file():
        raise UnsupportedShopError(f"{shop} has no pos.sqlite, and needs ingestion")
    dataset = SqliteDataset(
        database,
        hidden_columns=HIDDEN_COLUMNS,
        reference_tables={"calendar": _load_calendar(data_dir / "calendar.csv")},
    )
    rulebook = agent_rulebook((data_dir / "rulebook.md").read_text(encoding="utf-8"))
    documents = [
        *rulebook_documents(rulebook),
        Document("README.md", (directory / "README.md").read_text(encoding="utf-8")),
    ]
    return ShopContext(dataset, documents)
