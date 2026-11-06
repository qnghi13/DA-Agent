"""prepare_dataset: ingest, propose, build, and parse-rate feedback, with a fake Runner."""

import json
from pathlib import Path

from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
from da_agent.ingestion.pipeline import parse_problems, prepare_dataset
from da_agent.ingestion.report import ColumnProfile, ColumnReport, IngestReport, TableReport
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.llm.types import LLMRequest, LLMResponse, PermanentLLMError, Usage
from da_agent.runner.runner import BuildResult, BuildRun, IngestResult, IngestRun

REPORT = IngestReport(
    tables=[
        TableReport(
            name="don",
            sources=[],
            row_count=2,
            columns=[ColumnReport(name="Tổng", profile=ColumnProfile(non_null=2))],
        )
    ],
    documents=[{"path": "README.md", "text": "notes"}],
)
GOOD = json.dumps(
    {"tables": [{"source": "don", "name": "orders", "columns": [
        {"source": "Tổng", "name": "total", "type": "money"}]}]}
)  # fmt: skip


def build_report(parsed: int) -> BuildReport:
    column = ColumnBuild(name="total", source="Tổng", type="money", non_blank=100, parsed=parsed)
    return BuildReport(tables=[TableBuild(name="orders", source="don", columns=[column])])


class FakeRunner:
    def __init__(self, tmp_path: Path, builds: list[BuildReport], ingest_ok: bool = True) -> None:
        self.tmp_path = tmp_path
        self.builds = list(builds)
        self.ingest_ok = ingest_ok
        self.specs: list = []

    def ingest(self, request: IngestRun) -> IngestResult:
        out = self.tmp_path / "ing"
        out.mkdir(exist_ok=True)
        if not self.ingest_ok:
            return IngestResult(status="error", exit_code=1, stderr="boom", report=None,
                                output_dir=None, duration_seconds=1)  # fmt: skip
        return IngestResult(status="ok", exit_code=0, stderr="", report=REPORT, output_dir=out,
                            duration_seconds=1)  # fmt: skip

    def build(self, request: BuildRun) -> BuildResult:
        self.specs.append(request.spec)
        out = self.tmp_path / f"build{len(self.specs)}"
        out.mkdir()
        return BuildResult(status="ok", exit_code=0, stderr="", report=self.builds.pop(0),
                           output_dir=out, duration_seconds=1)  # fmt: skip


class Scripted:
    provider = "fake"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return LLMResponse(content=step, usage=Usage(input_tokens=10, output_tokens=5))


def llm(script: list) -> tuple[LLMClient, Scripted]:
    adapter = Scripted(script)
    return LLMClient(
        [Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0
    ), adapter


def test_a_good_spec_is_built_once(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, [build_report(parsed=100)])
    client, _ = llm([GOOD])

    prepared = prepare_dataset("up", runner, client, run_id="r")  # type: ignore[arg-type]

    assert prepared.status == "ok"
    assert prepared.proposal is not None and prepared.proposal.attempts == 1
    assert prepared.build is not None and prepared.build.output_dir == tmp_path / "build1"


def test_low_parse_rates_go_back_to_the_model_and_old_builds_are_removed(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, [build_report(parsed=60), build_report(parsed=100)])
    client, adapter = llm([GOOD, GOOD])

    prepared = prepare_dataset("up", runner, client, run_id="r")  # type: ignore[arg-type]

    assert prepared.proposal is not None and prepared.proposal.attempts == 2
    assert "parsed 60%" in (adapter.requests[-1].messages[-1].content or "")
    assert prepared.build is not None and prepared.build.output_dir == tmp_path / "build2"
    assert not (tmp_path / "build1").exists()


def test_a_failed_ingest_stops_early(tmp_path: Path) -> None:
    client, adapter = llm([])

    prepared = prepare_dataset("up", FakeRunner(tmp_path, [], ingest_ok=False), client, run_id="r")  # type: ignore[arg-type]

    assert prepared.status == "ingest_failed"
    assert adapter.requests == []


def test_a_model_failure_falls_back_to_text_columns(tmp_path: Path) -> None:
    runner = FakeRunner(tmp_path, [build_report(parsed=100)])
    client, _ = llm([PermanentLLMError("bad key")])

    prepared = prepare_dataset("up", runner, client, run_id="r")  # type: ignore[arg-type]

    assert prepared.status == "fallback"
    assert runner.specs[-1].tables == []


def test_parse_problems_name_the_column_rate_and_failing_values() -> None:
    report = build_report(parsed=90)
    report.tables[0].columns[0].failed_samples = ["abc"]

    problems = parse_problems(report, min_parse_rate=0.98)

    assert problems == ["orders.total (money): parsed 90% of 100 values; failing values: ['abc']"]
