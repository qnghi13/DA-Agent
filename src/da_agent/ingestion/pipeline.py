"""Turn one upload into a clean dataset: ingest, propose a spec, build, and repair.

Runs in the Worker. Parsing and building happen in sandboxes through the Runner, and the model only
proposes declarative specs. Spec approval by a user comes later; evals accept the result.
"""

import shutil
from dataclasses import dataclass
from typing import Literal

from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.propose import ProposalResult, propose_spec
from da_agent.ingestion.spec import DatasetSpec
from da_agent.llm.client import LLMClient, RunBudget
from da_agent.runner.runner import BuildResult, BuildRun, IngestResult, IngestRun, Runner

MIN_PARSE_RATE = 0.98

PrepareStatus = Literal["ok", "problems", "fallback", "ingest_failed", "build_failed"]


@dataclass
class PreparedDataset:
    status: PrepareStatus
    ingest: IngestResult
    proposal: ProposalResult | None = None
    build: BuildResult | None = None


def parse_problems(report: BuildReport, min_parse_rate: float = MIN_PARSE_RATE) -> list[str]:
    problems = []
    for table in report.tables:
        if table.error:
            problems.append(f"{table.name}: build failed: {table.error}")
        for column in table.columns:
            if column.non_blank and column.parse_rate < min_parse_rate:
                problems.append(
                    f"{table.name}.{column.name} ({column.type}): parsed "
                    f"{column.parse_rate:.0%} of {column.non_blank} values; "
                    f"failing values: {column.failed_samples}"
                )
    return problems


def prepare_dataset(
    upload_id: str,
    runner: Runner,
    llm: LLMClient,
    *,
    run_id: str,
    description: str = "",
    max_attempts: int = 3,
    min_parse_rate: float = MIN_PARSE_RATE,
    budget: RunBudget | None = None,
) -> PreparedDataset:
    ingested = runner.ingest(IngestRun(upload_id=upload_id))
    if ingested.status != "ok" or ingested.report is None or ingested.output_dir is None:
        return PreparedDataset(status="ingest_failed", ingest=ingested)
    report, ingest_id = ingested.report, ingested.output_dir.name
    documents = list(report.documents)
    if description:
        documents.append({"path": "user description", "text": description})

    builds: list[tuple[DatasetSpec, BuildResult]] = []

    def validate(spec: DatasetSpec) -> list[str]:
        built = runner.build(BuildRun(ingest_id=ingest_id, spec=spec))
        builds.append((spec, built))
        if built.status != "ok" or built.report is None:
            return [f"building the spec failed: {built.stderr[-500:]}"]
        return parse_problems(built.report, min_parse_rate)

    proposal = propose_spec(
        report, documents, llm, run_id=run_id, validate=validate,
        max_attempts=max_attempts, budget=budget,
    )  # fmt: skip

    chosen = next((b for s, b in reversed(builds) if s == proposal.spec), None)
    status: PrepareStatus = "ok" if not proposal.problems and not proposal.error else "problems"
    if chosen is None or chosen.status != "ok":
        # The model failed, or its spec never built: keep every column as text rather than nothing.
        fallback = DatasetSpec(tables=[])
        chosen = runner.build(BuildRun(ingest_id=ingest_id, spec=fallback))
        builds.append((fallback, chosen))
        status = "fallback"
    for _, built in builds:
        if built is not chosen and built.output_dir is not None:
            shutil.rmtree(built.output_dir, ignore_errors=True)
    if chosen.status != "ok":
        status = "build_failed"
    return PreparedDataset(status=status, ingest=ingested, proposal=proposal, build=chosen)
