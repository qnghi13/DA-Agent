"""The local layout eval: a model proposes layouts, local ingests try them, the best is kept."""

import json
import re
from pathlib import Path

import openpyxl

from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.llm.types import LLMRequest, LLMResponse, Usage
from eval.ingestion.raw_only import with_model


class FamilyEcho:
    """Replies with a layout for the first family id it finds in the prompt."""

    provider = "fake"

    def __init__(self, entry: dict) -> None:
        self.entry = entry
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append(request)
        prompt = request.messages[1].content or ""
        family = re.search(r'<family id="([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
        reply = json.dumps({"families": [{"family": family, **self.entry}]})
        return LLMResponse(content=reply, usage=Usage(input_tokens=10, output_tokens=5))


def test_the_best_layout_output_is_kept_and_candidates_are_removed(tmp_path: Path) -> None:
    upload = tmp_path / "shop"
    upload.mkdir()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Scores"
    for row in [
        ["Class", "7A1", "Subject", "Math"],
        [None, None, None, "TERM I", None, "TERM II", None],
        ["No", "Code", "Name", "TX1", "CK", "TX1", "CK"],
        [1, "S1", "An", 7, 8, 6, 9],
    ]:
        sheet.append(row)
    book.save(upload / "book.xlsx")
    adapter = FamilyEcho({"context": [{"name": "klass", "source": "cell", "cell": "B1"}]})
    client = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)

    row = with_model(
        upload, tmp_path / "out" / "shop", client, run_id="shop", candidates=1, rounds=1
    )

    assert row["kept"] == "layout"
    assert (tmp_path / "out" / "shop" / "layout.json").exists()
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["shop"]


def test_the_synthetic_layouts_are_read_right_by_their_hand_written_layout(tmp_path: Path) -> None:
    from da_agent.ingestion.layout_spec import LayoutSpec
    from da_agent.ingestion.raw import ingest
    from eval.ingestion.layout_synthetic import SHOP, generate
    from eval.ingestion.layout_targets import load_targets, score_dataset

    uploads = generate(tmp_path)
    found = {
        f.sources[0].split("#")[0]: f.family
        for f in ingest(uploads / SHOP, tmp_path / "probe").layout
    }
    layout = LayoutSpec.model_validate(
        {
            "families": [
                {
                    "family": found["HR.xlsx"],
                    "sections": "department",
                    "reshape": {"kind": "pair_rows", "column": "In/Out", "values": ["In", "Out"]},
                    "then": {
                        "kind": "unpivot",
                        "first": "1",
                        "last": "31",
                        "key": "day",
                        "value": "time",
                    },
                },
                {
                    "family": found["Orders.xlsx"],
                    "context": [{"name": "month", "source": "sheet_name"}],
                    "reshape": {"kind": "split_cell", "name": "weekday"},
                },
                {"family": found["Sales.xlsx"]},
            ]
        }
    )

    ingest(uploads / SHOP, tmp_path / "out", layout=layout)

    results = score_dataset(
        tmp_path / "out" / "raw.duckdb", load_targets(tmp_path / "targets.jsonl")
    )
    assert [r.passed for r in results] == [True] * 6
