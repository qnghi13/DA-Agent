"""The version job: check, propose, build, and apply a new dataset version.

Design: docs/design/2026-11-21-add-files.md. Step `check` ingests every file of the version,
matches its raw tables to the current version by header, builds with the current spec, and
decides in code: apply at once, wait for a choice on overlap, or propose new tables and repairs.
Step `build` applies the user's choices, builds, and applies. The model only proposes; code merges
its output and writes `drop_overlap` itself.

A failure marks the version failed and the job completes; the current version keeps serving
(ADR 0019). An outage of the Runner or the model provider is no failure of the files: the job is
retried with the upload kept, and only the last attempt fails the version. Only `apply` changes
what runs read, in one transaction, and every status change expects the status the step owns, so
a retried or late step never applies twice or revives a discarded version.
"""

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from opentelemetry import trace
from pydantic import ValidationError

from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.pipeline import PreparedDataset, parse_problems
from da_agent.ingestion.propose import ProposalResult
from da_agent.ingestion.propose_changes import propose_new_tables, propose_repairs
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import DatasetSpec
from da_agent.ingestion.version_review import (
    blocking_tables,
    cover_raw_tables,
    invalid_choices,
    overlap_keys,
    pin_kept_tables,
    replaced_files,
    stale_metrics,
    table_names,
    version_changes,
    with_overlap_drops,
)
from da_agent.ingestion.versioning import (
    QualityProblem,
    RepairChange,
    TableChange,
    TableHeader,
    apply_repairs,
    headers,
    match_tables,
    merge_new_tables,
    quality_problems,
    rename_sources,
)
from da_agent.llm.client import LLMClient, RunBudget
from da_agent.llm.types import AllRoutesFailedError
from da_agent.runner.client import UNAVAILABLE
from da_agent.runner.runner import (
    NOT_STARTED,
    BuildResult,
    BuildRun,
    IngestResult,
    IngestRun,
    JobResult,
    SandboxRunner,
)
from da_agent.storage.dataset_versions import DatasetVersionStore, VersionRecord
from da_agent.storage.datasets import DatasetStateError, DatasetStore
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job
from da_agent.storage.memory import MemoryStore
from da_agent.worker.datasets import DIRECTORY_NAME, FAILURES, _dataset_id, _install, _summary
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.worker import PermanentJobError, RetryableJobError

UNPROPOSED_NOTE = "No spec was proposed for this table, so its columns are text."
# The status each step owns: it acts only on a version in that status.
STEP_STATUS = {"check": "checking", "build": "building"}


class _VersionFailed(Exception):
    """The version cannot go on; the message is shown to the user."""


class _Outage(Exception):
    """The Runner or the model provider is down: retry the step, keep the upload."""


def _unavailable(result: JobResult[Any]) -> bool:
    """A sandbox that is down or never started, as exports and queries detect it."""
    return result.status == "error" and (
        result.stderr == UNAVAILABLE or result.stderr.startswith(NOT_STARTED)
    )


@dataclass
class _Matched:
    report: IngestReport
    ingested: IngestResult
    changes: list[TableChange]
    spec: DatasetSpec  # the current spec, renamed to this version's raw tables
    new_sources: tuple[str, ...]  # stored names of the version's new files, in upload order
    names_before: dict[str, str]

    def names(self, kind: str) -> set[str]:
        """Clean names of the tables of one kind; a removed table keeps its old name."""
        now = {t.source: t.name for t in self.spec.tables}
        names = self.names_before if kind == "removed" else now
        return {names.get(c.raw_name, c.raw_name) for c in self.changes if c.kind == kind}

    def raw(self, kind: str) -> set[str]:
        return {c.raw_name for c in self.changes if c.kind == kind}


class VersionHandler:
    def __init__(
        self,
        *,
        url: str,
        schema: str | None = None,
        runner: SandboxRunner,
        llm: LLMClient,
        datasets_root: Path,
        uploads_root: Path,
        budgets: WorkspaceBudgets | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self.url, self.schema = url, schema
        self.runner, self.llm, self.budgets = runner, llm, budgets
        self.datasets_root, self.uploads_root = datasets_root, uploads_root
        self.tracer = tracer or trace.get_tracer("da_agent.worker.versions")

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        dataset_id, number, step = _target(job)
        with connect(self.url, schema=self.schema) as conn:
            store = DatasetVersionStore(conn)
            record = store.get(job.workspace_id, dataset_id, number)
            if record is None:
                raise PermanentJobError("dataset version not found")
            try:
                current = store.current(dataset_id)
            except DatasetStateError as error:
                raise PermanentJobError(str(error)) from error
            if lost.is_set():
                return {"status": "lost"}
            owned = STEP_STATUS[step]
            starts = ("uploaded", owned) if step == "check" else (owned,)
            if not store.mark(dataset_id, number, owned, expected=starts):
                # Discarded, or done by another attempt.
                return {"status": _status(store, job.workspace_id, record)}
            run = _VersionRun(self, conn, job, record, current, lost)
            try:
                return run.check() if step == "check" else run.build()
            except _VersionFailed as failure:
                return run.fail(str(failure))
            except (_Outage, AllRoutesFailedError) as outage:
                if job.attempts >= job.max_attempts:
                    # No retry is left: a failed version frees the slot; a stuck one blocks it.
                    return run.fail(FAILURES["unavailable"])
                raise RetryableJobError(UNAVAILABLE) from outage
            except RetryableJobError:
                raise  # the version stays in its step until the retry
            except Exception:
                # A crash must not leave the version open: one open version blocks new uploads.
                run.fail(FAILURES["version_failed"])
                raise
            finally:
                run.clean_staging()


def _target(job: Job) -> tuple[UUID, int, str]:
    number, step = job.payload.get("version"), job.payload.get("step")
    if not isinstance(number, int) or isinstance(number, bool) or step not in STEP_STATUS:
        raise PermanentJobError("the job names no valid dataset version step")
    return _dataset_id(job), number, str(step)


def _status(store: DatasetVersionStore, workspace_id: UUID, record: VersionRecord) -> str:
    fresh = store.get(workspace_id, record.dataset_id, record.version)
    return fresh.status if fresh else record.status


class _VersionRun:
    """One attempt at one step of one version."""

    def __init__(
        self,
        handler: VersionHandler,
        conn: psycopg.Connection,
        job: Job,
        record: VersionRecord,
        current: VersionRecord,
        lost: threading.Event,
    ) -> None:
        self.h, self.conn, self.store, self.job = handler, conn, DatasetVersionStore(conn), job
        self.record, self.current, self.lost = record, current, lost
        self.owned = STEP_STATUS[str(job.payload["step"])]
        self.name = f"{record.dataset_id}-v{record.version}"
        self.staging: list[Path] = []
        self.last_build: BuildResult | None = None
        self._budget: RunBudget | None = None

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        with self.h.tracer.start_as_current_span(name) as span:
            span.set_attribute("da_agent.job_id", str(self.job.id))
            span.set_attribute("da_agent.dataset_id", str(self.record.dataset_id))
            span.set_attribute("da_agent.dataset_version", self.record.version)
            yield

    def ingest(self, upload_dir: str) -> tuple[IngestResult, IngestReport, str]:
        if not DIRECTORY_NAME.fullmatch(upload_dir):
            raise _VersionFailed(FAILURES["ingest_failed"])
        with self.span("version.ingest"):
            result = self.h.runner.ingest(IngestRun(upload_id=upload_dir))
        if result.output_dir is not None:
            self.staging.append(result.output_dir)
        if _unavailable(result):
            raise _Outage(result.stderr)
        if result.status != "ok" or result.report is None or result.output_dir is None:
            raise _VersionFailed(FAILURES["ingest_failed"])
        return result, result.report, result.output_dir.name

    def build_spec(self, ingest_id: str, spec: DatasetSpec, new: tuple[str, ...]) -> BuildResult:
        with self.span("version.build"):
            # Upload order: each new file is checked against the new files before it too.
            request = BuildRun(ingest_id=ingest_id, spec=spec, new_sources=list(new))
            result = self.h.runner.build(request)
        if result.output_dir is not None:
            self.staging.append(result.output_dir)
        if _unavailable(result):
            # Raised here, so a proposal's validation never shows an outage to the model.
            raise _Outage(result.stderr)
        return result

    def built(self, ingest_id: str, spec: DatasetSpec, new: tuple[str, ...]) -> BuildReport:
        result = self.build_spec(ingest_id, spec, new)
        report = result.report
        if result.status != "ok" or report is None or any(t.error for t in report.tables):
            raise _VersionFailed(FAILURES["build_failed"])
        self.last_build = result
        return report

    def previous_headers(self) -> list[TableHeader]:
        """The current version's raw headers. Version 1 from before versions has none stored, so
        its folder is ingested once and the headers are kept on its row."""
        if self.current.tables:
            return [TableHeader.model_validate(h) for h in self.current.tables]
        _, report, _ = self.ingest(self.current.upload_dir)
        found = headers(report)
        stored = [h.model_dump(mode="json") for h in found]
        status = self.current.status
        self.store.mark(self.current.dataset_id, self.current.version, status,
                        expected=(status,), tables=stored)  # fmt: skip
        return found

    def match(self, files: list[dict[str, Any]]) -> _Matched:
        new = tuple(
            str(f.get("stored_as")) for f in files if f.get("added_in") == self.record.version
        )
        if not new:
            # Every upload brings a file. Without `added_in` all rows would count as old and the
            # version would apply unchecked.
            raise _VersionFailed(FAILURES["version_failed"])
        ingested, report, _ = self.ingest(self.record.upload_dir)
        previous = self.previous_headers()
        with self.span("version.match"):
            current = DatasetSpec.model_validate(self.current.spec or {"tables": []})
            changes = match_tables(previous, report, frozenset(new), current)
            spec = pin_kept_tables(rename_sources(current, changes), current, previous, changes)
            before = table_names(current, previous)
        return _Matched(report, ingested, changes, spec, new, before)

    def check(self) -> dict[str, Any]:
        m = self.match(self.record.files)
        ingest_id = m.ingested.output_dir.name  # type: ignore[union-attr]
        report = self.built(ingest_id, m.spec, m.new_sources)
        quality = quality_problems(report, m.names("grown"))
        spec, shown, proposal, unproposed = m.spec, report, None, []
        if m.raw("new"):
            proposal, shown_build = self.propose_tables(m, ingest_id)
            spec, unproposed = cover_raw_tables(proposal.spec, m.report)
            shown = shown_build or report
        repairs: list[RepairChange] = []
        repair_problem = None
        if quality:
            repairs, repair_problem = self.propose_repairs(m, ingest_id, report, quality)
        removed = m.names("removed")
        stale = stale_metrics(self.metrics(), removed, shown) if removed else {}
        changes = version_changes(m.changes, spec, m.names_before, report, quality, stale)
        for entry in changes:
            if entry["table"] in unproposed:
                entry["note"] = UNPROPOSED_NOTE
        fields = self.fields(m, spec, shown, proposal)
        if repair_problem:
            fields["summary"]["repair_problem"] = repair_problem
        fields["changes"] = changes
        fields["repairs"] = [r.model_dump(mode="json") for r in repairs]
        if m.raw("new") or removed or quality:
            status = "proposed"
        elif blocking_tables(changes):
            status = "needs_choice"
        else:
            return self.install_and_apply(fields)
        if not self.mark(status, **fields):
            return self.not_mine()
        return {"status": status}

    def build(self) -> dict[str, Any]:
        choices = self.record.choices or {}
        chosen: dict[str, str] = dict(choices.get("tables") or {})
        entries: list[dict[str, Any]] = self.record.changes or []
        if invalid_choices(entries, chosen):
            raise _VersionFailed(FAILURES["choices_invalid"])
        files = self.replace_files(chosen, choices.get("replace_files") or {})
        m = self.match(files)
        ingest_id = m.ingested.output_dir.name  # type: ignore[union-attr]
        try:
            proposed = DatasetSpec.model_validate(
                self.record.spec_edits or self.record.spec or {"tables": []}
            )
            spec = merge_new_tables(m.spec, proposed, m.raw("new"))
            spec, _ = cover_raw_tables(spec, m.report)
            accepted = [RepairChange.model_validate(r) for r in self.record.repairs or []]
            spec = apply_repairs(spec, accepted)
            drop = {table for table, choice in chosen.items() if choice == "drop_overlap"}
            spec = with_overlap_drops(
                spec, drop, m.report, list(m.new_sources), overlap_keys(entries)
            )
            spec = DatasetSpec.model_validate(spec.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as error:
            raise _VersionFailed(FAILURES["choices_invalid"]) from error
        report = self.built(ingest_id, spec, m.new_sources)
        if quality_problems(report, m.names("grown")) and not choices.get("apply_anyway"):
            raise _VersionFailed(FAILURES["quality_failed"])
        return self.install_and_apply(self.fields(m, spec, report, None) | {"files": files})

    def replace_files(self, chosen: dict[str, str], names: dict[str, Any]) -> list[dict[str, Any]]:
        """Drop each file the user chose to replace from the version: from its file list, and its
        link from the version's own folder. Only a file of the current version can be replaced,
        so a retry, after the link is gone, finds the same files."""
        upload_dir = self.record.upload_dir
        try:
            replaced = replaced_files(chosen, names, self.current.files)
        except ValueError as error:
            raise _VersionFailed(FAILURES["choices_invalid"]) from error
        if replaced and (upload_dir == self.current.upload_dir
                         or not DIRECTORY_NAME.fullmatch(upload_dir)):  # fmt: skip
            raise _VersionFailed(FAILURES["choices_invalid"])
        for name in replaced:
            (self.h.uploads_root / upload_dir / name).unlink(missing_ok=True)
        return [f for f in self.record.files if f.get("stored_as") not in replaced]

    def budget(self) -> RunBudget | None:
        if self.h.budgets is None:
            return None
        if self._budget is None:
            try:
                self._budget = self.h.budgets.for_job(self.job)
            except PermanentJobError as error:
                raise _VersionFailed(str(error)) from error
        return self._budget

    def propose_tables(
        self, m: _Matched, ingest_id: str
    ) -> tuple[ProposalResult, BuildReport | None]:
        new_raw = m.raw("new")
        builds: list[tuple[DatasetSpec, BuildResult]] = []

        def validate(spec: DatasetSpec) -> list[str]:
            result = self.build_spec(ingest_id, spec, m.new_sources)
            builds.append((spec, result))
            if result.status != "ok" or result.report is None:
                return ["building the spec failed"]
            tables = [t for t in result.report.tables if t.source in new_raw]
            return parse_problems(result.report.model_copy(update={"tables": tables}))

        documents = list(m.report.documents)
        dataset = DatasetStore(self.conn).get(self.record.dataset_id, self.job.workspace_id)
        if dataset is not None and dataset.description:
            documents.append({"path": "user description", "text": dataset.description})
        budget = self.budget()
        with self.span("version.propose"):
            result = propose_new_tables(
                m.report, new_raw, m.spec, documents, self.h.llm, run_id=str(self.job.id),
                validate=validate, budget=budget,
            )  # fmt: skip
        if result.error:
            raise _VersionFailed(FAILURES["proposal_failed"])
        shown = next((b.report for s, b in reversed(builds) if s == result.spec), None)
        return result, shown

    def propose_repairs(
        self,
        m: _Matched,
        ingest_id: str,
        report: BuildReport,
        quality: dict[str, list[QualityProblem]],
    ) -> tuple[list[RepairChange], str | None]:
        def validate(spec: DatasetSpec) -> list[str]:
            result = self.build_spec(ingest_id, spec, m.new_sources)
            if result.status != "ok" or result.report is None:
                return ["building the spec failed"]
            left = quality_problems(result.report, set(quality))
            return [p.message for problems in left.values() for p in problems]

        budget = self.budget()
        with self.span("version.propose"):
            return propose_repairs(
                report, quality, m.spec, self.h.llm, run_id=str(self.job.id),
                validate=validate, budget=budget,
            )  # fmt: skip

    def metrics(self) -> list[tuple[str, dict[str, Any]]]:
        entries = MemoryStore(self.conn).approved(self.job.workspace_id, self.record.dataset_id)
        return [
            (e.name or "", e.definition)
            for e in entries
            if e.kind == "metric" and e.definition is not None
        ]

    def fields(
        self, m: _Matched, spec: DatasetSpec, report: BuildReport, proposal: ProposalResult | None
    ) -> dict[str, Any]:
        """What the version holds once applied: its spec, summary, documents, and raw headers."""
        status = "problems" if parse_problems(report) else "ok"
        prepared = PreparedDataset(status=status, ingest=m.ingested, proposal=proposal, spec=spec)
        return {
            "spec": spec.model_dump(mode="json"),
            "summary": _summary(prepared, report),
            "documents": list(m.report.documents),
            "tables": [h.model_dump(mode="json") for h in headers(m.report)],
        }

    def install_and_apply(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Store what the version will hold, install its build as `<id>-v<n>`, and apply it in
        one transaction. A retry after the install replaces the directory and applies once."""
        if not self.mark(self.owned, **fields):
            return self.not_mine()
        output = self.last_build.output_dir if self.last_build else None
        if output is None:
            raise _VersionFailed(FAILURES["build_failed"])
        with self.span("version.install"):
            _install(output, self.h.datasets_root, self.name)
        self.staging.remove(output)
        if self.lost.is_set():
            return {"status": "lost"}  # the next attempt replaces the directory and applies
        record = self.record
        if not self.store.apply(record.dataset_id, record.version, self.name,
                                expected=(self.owned,)):  # fmt: skip
            # Another attempt failed it or the user discarded it: its directory is not wanted.
            shutil.rmtree(self.h.datasets_root / self.name, ignore_errors=True)
            return self.not_mine()
        return {"status": "applied"}

    def fail(self, message: str) -> dict[str, Any]:
        """Mark the version failed and remove what it alone holds: its upload folder and any
        build installed for it. The current version's folders are never touched."""
        record = self.record
        if not self.mark("failed", error=message):
            return self.not_mine()
        upload_dir = record.upload_dir
        if upload_dir != self.current.upload_dir and DIRECTORY_NAME.fullmatch(upload_dir):
            shutil.rmtree(self.h.uploads_root / upload_dir, ignore_errors=True)
        if self.name != self.current.build_dir:
            shutil.rmtree(self.h.datasets_root / self.name, ignore_errors=True)
        return {"status": "failed"}

    def mark(self, status: str, **fields: Any) -> bool:
        """Change the version only while this attempt still holds the job and the version is
        still in the step's status. A worker that lost its lease leaves it to the next attempt."""
        if self.lost.is_set():
            return False
        record = self.record
        return self.store.mark(
            record.dataset_id, record.version, status, expected=(self.owned,), **fields
        )

    def not_mine(self) -> dict[str, Any]:
        if self.lost.is_set():
            return {"status": "lost"}
        return {"status": _status(self.store, self.job.workspace_id, self.record)}

    def clean_staging(self) -> None:
        for path in self.staging:
            shutil.rmtree(path, ignore_errors=True)
