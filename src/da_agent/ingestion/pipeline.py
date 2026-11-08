"""Turn one upload into a clean dataset: ingest, propose a spec, build, and repair.

Runs in the Worker. Parsing and building happen in sandboxes through the Runner, and the model only
proposes declarative specs. Spec approval by a user comes later; evals accept the result.
"""

import shutil
from dataclasses import dataclass, field
from typing import Literal

from da_agent.ingestion.build import BuildReport, ExcludedRule
from da_agent.ingestion.propose import ProposalResult, propose_spec
from da_agent.ingestion.spec import DatasetSpec, TableSpec, keep_source_names
from da_agent.llm.client import LLMClient, RunBudget
from da_agent.runner.runner import (
    BuildResult,
    BuildRun,
    IngestResult,
    IngestRun,
    SandboxRunner,
)

MIN_PARSE_RATE = 0.98
# Subtotal and note rows in the dev shops are at most 2% of a table. A rule that removes far more
# is probably removing records.
MAX_EXCLUDED_SHARE = 0.10

PrepareStatus = Literal["ok", "problems", "fallback", "ingest_failed", "build_failed"]


@dataclass
class PreparedDataset:
    status: PrepareStatus
    ingest: IngestResult
    proposal: ProposalResult | None = None
    build: BuildResult | None = None
    # The spec that was built, which can differ from the proposal when rules were dropped.
    spec: DatasetSpec | None = None
    dropped_rules: list[str] = field(default_factory=list)
    # Tables whose exact repeats were removed because a key proved them double entries.
    deduplicated: list[str] = field(default_factory=list)


def _record_problems(
    table: str, raw_rows: int, rule: ExcludedRule, max_excluded_share: float
) -> list[str]:
    """Signs that an exclude rule removes records rather than summary or note rows."""
    problems = []
    if rule.full_rows > rule.rows / 2:
        problems.append(
            f"{table}: exclude rule {rule.reason!r} removes {rule.full_rows} rows filled like "
            "ordinary records; exclude only summary or note rows, and keep records even when a "
            "column such as a note is filled"
        )
    if rule.with_identifier > rule.rows / 2:
        problems.append(
            f"{table}: exclude rule {rule.reason!r} removes {rule.with_identifier} rows that "
            f"have their own {rule.identifier!r}, so they look like records; exclude only summary "
            "or note rows, and keep records with missing fields"
        )
    if raw_rows and rule.rows / raw_rows > max_excluded_share:
        problems.append(
            f"{table}: exclude rule {rule.reason!r} removed {rule.rows} of {raw_rows} rows "
            f"({rule.rows / raw_rows:.0%}); exclude only summary or note rows"
        )
    return problems


def _without_record_rules(spec: DatasetSpec, report: BuildReport) -> tuple[DatasetSpec, list[str]]:
    """Drop exclude rules that still remove records. Keeping a subtotal is safer than losing
    records, and both the spec and `_excluded` stay open to review."""
    bad = {
        (table.name, rule.position): rule.reason
        for table in report.tables
        for rule in table.excluded
        if _record_problems(table.name, table.raw_rows, rule, MAX_EXCLUDED_SHARE)
    }
    if not bad:
        return spec, []
    tables, dropped = [], []
    for table in spec.tables:
        kept = []
        for position, rule in enumerate(table.exclude_rows, start=1):
            if (table.name, position) in bad:
                dropped.append(f"{table.name}: {bad[table.name, position]!r}")
            else:
                kept.append(rule)
        tables.append(table.model_copy(update={"exclude_rows": kept}))
    if not dropped:
        return spec, []
    return spec.model_copy(update={"tables": tables}), dropped


def _with_dedupe(spec: DatasetSpec, report: BuildReport) -> tuple[DatasetSpec, list[str]]:
    """Turn on exact dedupe for tables whose repeated rows a key proves to be double entries, such
    as two identical rows with the same order id and line number. Code decides this, not the
    model: identical rows cannot be separate records when a key tells all distinct rows apart."""
    proven = {
        table.name: table.source
        for table in report.tables
        if table.duplicate_rows and table.duplicate_key
    }
    tables, changed = [], []
    for table in spec.tables:
        if table.name in proven and table.dedupe == "none":
            table = table.model_copy(update={"dedupe": "exact"})
            changed.append(table.name)
        tables.append(table)
    listed = {table.name for table in spec.tables}
    for name, source in proven.items():
        if name not in listed:
            tables.append(TableSpec(source=source, name=name, dedupe="exact"))
            changed.append(name)
    if not changed:
        return spec, []
    return spec.model_copy(update={"tables": tables}), changed


def parse_problems(
    report: BuildReport,
    min_parse_rate: float = MIN_PARSE_RATE,
    max_excluded_share: float = MAX_EXCLUDED_SHARE,
) -> list[str]:
    """Build problems a model can fix: failed tables, low parse rates, subtotals still in a table,
    and exclude rules that remove records."""
    problems = []
    for table in report.tables:
        if table.error:
            problems.append(f"{table.name}: build failed: {table.error}")
        for check in table.subtotal_checks:
            if check.kept:
                problems.append(
                    f"{table.name}: {check.kept} rows with only {check.columns} filled are still "
                    f"in the table, and {check.sums_above} of the {check.rows} rows of that shape "
                    "equal the sum of the rows above them, so they look like subtotals or "
                    "totals; exclude them with exclude_rows unless they are records"
                )
        for rule in table.excluded:
            problems += _record_problems(table.name, table.raw_rows, rule, max_excluded_share)
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
    runner: SandboxRunner,
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

    spec = proposal.spec
    chosen = next((b for s, b in reversed(builds) if s == spec), None)
    status: PrepareStatus = "ok" if not proposal.problems and not proposal.error else "problems"
    dropped: list[str] = []
    deduplicated: list[str] = []
    if chosen is not None and chosen.status == "ok" and chosen.report is not None:
        safer, dropped = _without_record_rules(spec, chosen.report)
        safer, deduplicated = _with_dedupe(safer, chosen.report)
        if dropped or deduplicated:
            rebuilt = runner.build(BuildRun(ingest_id=ingest_id, spec=safer))
            builds.append((safer, rebuilt))
            if rebuilt.status == "ok":
                spec, chosen = safer, rebuilt
    if chosen is None or chosen.status != "ok":
        # The model failed, or its spec never built: keep every column as text rather than nothing.
        spec = DatasetSpec(tables=[])
        chosen = runner.build(BuildRun(ingest_id=ingest_id, spec=spec))
        builds.append((spec, chosen))
        status = "fallback"
    for _, built in builds:
        if built is not chosen and built.output_dir is not None:
            shutil.rmtree(built.output_dir, ignore_errors=True)
    if chosen.status != "ok":
        status = "build_failed"
    return PreparedDataset(
        status=status,
        ingest=ingested,
        proposal=proposal,
        build=chosen,
        spec=spec,
        dropped_rules=dropped,
        deduplicated=deduplicated,
    )


def rebuild_dataset(
    upload_id: str,
    runner: SandboxRunner,
    spec: DatasetSpec,
    *,
    min_parse_rate: float = MIN_PARSE_RATE,
) -> PreparedDataset:
    """Ingest an upload and build it with an approved spec, without the model. Used when files
    change under a spec the user already approved."""
    spec = keep_source_names(spec)
    ingested = runner.ingest(IngestRun(upload_id=upload_id))
    if ingested.status != "ok" or ingested.report is None or ingested.output_dir is None:
        return PreparedDataset(status="ingest_failed", ingest=ingested)
    ingest_id = ingested.output_dir.name
    built = runner.build(BuildRun(ingest_id=ingest_id, spec=spec))
    if built.status != "ok" or built.report is None:
        return PreparedDataset(status="build_failed", ingest=ingested, build=built, spec=spec)
    report = built.report
    deduped, deduplicated = _with_dedupe(spec, report)
    if deduplicated:
        rebuilt = runner.build(BuildRun(ingest_id=ingest_id, spec=deduped))
        if rebuilt.status == "ok" and rebuilt.report is not None:
            if built.output_dir is not None:
                shutil.rmtree(built.output_dir, ignore_errors=True)
            spec, built, report = deduped, rebuilt, rebuilt.report
        else:
            if rebuilt.output_dir is not None:
                shutil.rmtree(rebuilt.output_dir, ignore_errors=True)
            deduplicated = []
    problems = parse_problems(report, min_parse_rate)
    status: PrepareStatus = "problems" if problems else "ok"
    return PreparedDataset(
        status=status, ingest=ingested, build=built, spec=spec, deduplicated=deduplicated
    )
