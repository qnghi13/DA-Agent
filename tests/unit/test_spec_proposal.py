"""Proposing a DatasetSpec with an LLM: prompt content, parsing, and the repair loop."""

import json

from da_agent.ingestion.propose import propose_spec
from da_agent.ingestion.report import ColumnProfile, ColumnReport, IngestReport, TableReport
from da_agent.ingestion.spec import DatasetSpec
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.llm.types import LLMRequest, LLMResponse, PermanentLLMError, Usage


class ScriptedAdapter:
    provider = "fake"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return LLMResponse(content=step, usage=Usage(input_tokens=100, output_tokens=50))


REPORT = IngestReport(
    tables=[
        TableReport(
            name="don",
            sources=[],
            row_count=2,
            columns=[
                ColumnReport(
                    name="Tổng",
                    profile=ColumnProfile(
                        non_null=2, distinct=2, samples=["22,000"], patterns={"number_grouped": 2}
                    ),
                ),
                ColumnReport(name="SĐT", profile=ColumnProfile(non_null=2, sensitive=True)),
            ],
        )
    ]
)
DOCUMENTS = [{"path": "README.md", "text": "Shop notes: Tổng is in VND."}]
GOOD = json.dumps(
    {"tables": [{"source": "don", "name": "orders", "columns": [
        {"source": "Tổng", "name": "total", "type": "money"}]}]}
)  # fmt: skip


def propose(script: list, validate=lambda spec: [], max_attempts: int = 3):
    adapter = ScriptedAdapter(script)
    llm = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    result = propose_spec(
        REPORT, DOCUMENTS, llm, run_id="r1", validate=validate, max_attempts=max_attempts
    )
    return result, adapter


def last_user_message(adapter: ScriptedAdapter) -> str:
    return adapter.requests[-1].messages[-1].content or ""


def test_a_valid_proposal_is_accepted_at_once() -> None:
    result, _ = propose([f"```json\n{GOOD}\n```"])

    assert result.attempts == 1
    assert result.problems == []
    assert result.spec.tables[0].columns[0].type == "money"


def test_the_prompt_frames_the_profile_and_documents_as_data() -> None:
    _, adapter = propose([GOOD])

    prompt = "\n".join(m.content or "" for m in adapter.requests[0].messages)
    assert '"don"' in prompt and '"Tổng"' in prompt and "22,000" in prompt
    assert '<document name="README.md">' in prompt
    assert '"sensitive": true' in prompt


def test_the_prompt_shows_rare_row_shapes_with_examples() -> None:
    from da_agent.ingestion.report import RowShape

    REPORT.tables[0].rare_shapes = [
        RowShape(columns=["Tổng"], rows=3, examples=[{"_row": "9", "Tổng": "TỔNG NGÀY 01/02"}])
    ]
    try:
        _, adapter = propose([GOOD])
    finally:
        REPORT.tables[0].rare_shapes = []

    prompt = "\n".join(m.content or "" for m in adapter.requests[0].messages)
    assert "TỔNG NGÀY 01/02" in prompt


def test_invalid_json_is_sent_back_for_repair() -> None:
    result, adapter = propose(['{"tables": [{"source": "don", "name": "Bad Name"}]}', GOOD])

    assert result.attempts == 2
    assert result.problems == []
    assert "name" in last_user_message(adapter)


def test_problems_against_the_profile_are_sent_back() -> None:
    ghost = json.dumps({"tables": [{"source": "ghost", "name": "ghost"}]})

    result, adapter = propose([ghost, GOOD])

    assert result.attempts == 2
    assert "'ghost' is not in the raw profile" in last_user_message(adapter)


def test_validation_problems_such_as_parse_rates_are_sent_back() -> None:
    checks = iter([["orders.total parsed 50% of 2 values; failing values: ['abc']"], []])

    result, adapter = propose([GOOD, GOOD], validate=lambda spec: next(checks))

    assert result.attempts == 2
    assert "parsed 50%" in last_user_message(adapter)
    assert result.problems == []


def test_after_the_last_attempt_the_best_spec_and_its_problems_are_returned() -> None:
    result, _ = propose([GOOD, GOOD], validate=lambda spec: ["still bad"], max_attempts=2)

    assert result.attempts == 2
    assert result.problems == ["still bad"]
    assert result.spec.tables[0].name == "orders"


def test_a_provider_failure_returns_an_empty_spec_and_the_error() -> None:
    result, _ = propose([PermanentLLMError("bad key")])

    assert result.spec == DatasetSpec(tables=[])
    assert result.error is not None and "bad key" in result.error


def test_a_partly_invalid_reply_keeps_its_valid_tables_and_asks_for_the_rest() -> None:
    partly = json.dumps(
        {"tables": [{"source": "don", "name": "orders", "columns": [
            {"source": "Tổng", "name": "total", "type": "money"},
            {"source": "SĐT", "name": "phone", "type": "python"}]}]}
    )  # fmt: skip
    built: list[DatasetSpec] = []

    def validate(spec: DatasetSpec) -> list[str]:
        built.append(spec)
        return []

    result, adapter = propose([partly, GOOD], validate=validate)

    assert [c.name for c in built[0].tables[0].columns] == ["total"]
    assert "column 'SĐT' dropped" in last_user_message(adapter)
    assert result.attempts == 2 and result.problems == []


def test_only_the_first_json_object_is_read() -> None:
    result, _ = propose([GOOD + "\n" + GOOD])

    assert result.problems == [] and result.attempts == 1


def test_broken_parts_are_dropped_and_the_rest_is_still_built() -> None:
    dangling = json.dumps(
        {"tables": [{"source": "don", "name": "orders", "columns": [
            {"source": "Tổng", "name": "total", "type": "money"}]}],
         "relationships": [{"from_table": "orders", "from_column": "total",
                            "to_table": "x", "to_column": "y"}]}
    )  # fmt: skip
    built: list[DatasetSpec] = []

    def validate(spec: DatasetSpec) -> list[str]:
        built.append(spec)
        return []

    result, _ = propose([dangling], validate=validate, max_attempts=1)

    assert built and built[0].relationships == []
    assert result.spec.tables[0].columns[0].type == "money"
    assert any("unknown column x.y" in p for p in result.problems)
