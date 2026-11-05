"""On-demand rule documents. The prompt lists their section titles, and search_rules fetches one.

Long rule documents cost tokens on every turn when they sit in the prompt. Splitting them by
heading lets the model fetch only the sections a question needs. See docs/adr/0008.
"""

import re
import unicodedata
from dataclasses import dataclass

from da_agent.agent.loop_types import Document

MAX_SECTIONS_PER_SEARCH = 2
_SECTION_HEADING = re.compile(r"^## +(.+?)\s*$")
_SECTION_NUMBER = re.compile(r"^(\d+)\b")


@dataclass(frozen=True)
class Section:
    title: str
    text: str


def split_sections(text: str) -> tuple[str, list[Section]]:
    """Split markdown at level-2 headings. Deeper headings stay inside their section."""
    preface: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        heading = _SECTION_HEADING.match(line)
        if heading:
            sections.append((heading.group(1), [line]))
        elif sections:
            sections[-1][1].append(line)
        else:
            preface.append(line)
    return "\n".join(preface).strip(), [
        Section(title, "\n".join(lines).strip()) for title, lines in sections
    ]


def normalize(text: str) -> str:
    """Lower case without diacritics, so 'cong no' matches 'Công nợ'."""
    text = text.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


class RuleIndex:
    """Sections of every on-demand document, searchable by title, number, or keywords."""

    def __init__(self, documents: list[Document]) -> None:
        self.sections: list[Section] = []
        for document in documents:
            if document.on_demand:
                self.sections.extend(split_sections(document.text)[1])

    def titles(self) -> list[str]:
        return [section.title for section in self.sections]

    def search(self, query: str) -> list[Section]:
        wanted = normalize(query).strip()
        if not wanted:
            return []
        by_title = [s for s in self.sections if self._title_matches(s.title, wanted)]
        if by_title:
            return by_title[:MAX_SECTIONS_PER_SEARCH]
        terms = [term for term in re.findall(r"\w+", wanted) if len(term) > 1]
        scored = [
            (self._score(section, terms), index) for index, section in enumerate(self.sections)
        ]
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])
        return [self.sections[index] for _, index in ranked[:MAX_SECTIONS_PER_SEARCH]]

    @staticmethod
    def _title_matches(title: str, wanted: str) -> bool:
        number = _SECTION_NUMBER.match(title)
        if number and wanted.rstrip(".") == number.group(1):
            return True
        return wanted in normalize(title)

    @staticmethod
    def _score(section: Section, terms: list[str]) -> int:
        title, text = normalize(section.title), normalize(section.text)
        return sum(3 * title.count(term) + text.count(term) for term in terms)
