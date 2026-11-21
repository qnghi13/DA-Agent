"""The version job: check a new dataset version, wait for the user when needed, and apply it.

The fake Runner runs the real ingest and build in this process, so no Docker is needed. Model
calls go to a scripted adapter. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from da_agent.ingestion.build import CLEAN_DATABASE, build
from da_agent.ingestion.raw import RAW_DATABASE, ingest
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, OverlapDrop, TableSpec
from da_agent.ingestion.versioning import RepairChange
from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route
from da_agent.runner.runner import BuildResult, BuildRun, IngestResult, IngestRun
from da_agent.storage.dataset_versions import DatasetVersionStore, VersionRecord
from da_agent.storage.datasets import DatasetStore
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.storage.memory import MemoryStore
from da_agent.storage.sandbox_dataset import BUILD_REPORT
from da_agent.storage.workspaces import set_daily_budget
from da_agent.telemetry.tracing import configure_tracing
from da_agent.worker.datasets import FAILURES
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.versions import VersionHandler
from tests.integration.conftest import POSTGRES_URL
from tests.unit.test_prepare_pipeline import Scripted

pytestmark = pytest.mark.integration

ORDERS = "id,total,day\n1,100,01/02/2026\n2,250,15/02/2026\n"
SPEC = DatasetSpec(
    tables=[
        TableSpec(
            source="orders",
            name="orders",
            columns=[
                ColumnSpec(source="id", name="id", type="integer"),
                ColumnSpec(source="total", name="total", type="money"),
                ColumnSpec(source="day", name="day", type="date", date_formats=["dmy"]),
            ],
        )
    ]
)
CUSTOMERS_REPLY = json.dumps(
    {"tables": [{"source": "customers", "name": "customers", "columns": [
        {"source": "name", "name": "name"}, {"source": "city", "name": "city"}]}]}
)  # fmt: skip


class Killed(BaseException):
    """Stands in for the worker process dying: nothing in the worker catches it."""


class InProcessRunner:
    """A fake Runner that runs the real ingest and build here, without a sandbox."""

    def __init__(self, uploads: Path, staging: Path, fail_ingest: set[str] | None = None) -> None:
        self.uploads, self.staging = uploads, staging
        self.fail_ingest = fail_ingest or set()
        self.builds: list[BuildRun] = []

    def _out(self, prefix: str) -> Path:
        out = self.staging / f"{prefix}-{uuid.uuid4().hex[:8]}"
        out.mkdir(parents=True)
        return out

    def ingest(self, request: IngestRun) -> IngestResult:
        if request.upload_id in self.fail_ingest:
            return IngestResult(status="error", exit_code=1, stderr="boom", report=None,
                                output_dir=None, duration_seconds=0)  # fmt: skip
        out = self._out("ingest")
        report = ingest(self.uploads / request.upload_id, out)
        (out / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
        return IngestResult(status="ok", exit_code=0, stderr="", report=report, output_dir=out,
                            duration_seconds=0)  # fmt: skip

    def build(self, request: BuildRun) -> BuildResult:
        self.builds.append(request)
        source = self.staging / request.ingest_id
        report = IngestReport.model_validate_json((source / "report.json").read_text("utf-8"))
        out = self._out("build")
        result = build(
            source / RAW_DATABASE, request.spec, report, out, frozenset(request.new_sources)
        )
        (out / BUILD_REPORT).write_text(result.model_dump_json(), encoding="utf-8")
        return BuildResult(status="ok", exit_code=0, stderr="", report=result, output_dir=out,
                           duration_seconds=0)  # fmt: skip


class Setup:
    def __init__(self, schema: str, conn: psycopg.Connection, workspace: UUID, tmp: Path) -> None:
        self.schema, self.conn, self.workspace = schema, conn, workspace
        self.uploads, self.datasets = tmp / "uploads", tmp / "datasets"
        self.runner = InProcessRunner(self.uploads, tmp / "staging")
        self.store = DatasetVersionStore(conn)
        self.dataset_id = self._ready_dataset()

    def _ready_dataset(self) -> UUID:
        """Version 1 as the normal upload path leaves it: no stored headers."""
        files = [{"name": "orders.csv", "stored_as": "orders.csv", "bytes": 10, "added_in": 1}]
        datasets = DatasetStore(self.conn)
        dataset_id = datasets.create(self.workspace, "Shop", "Orders of one shop.", files=files)
        folder = self.uploads / str(dataset_id)
        folder.mkdir(parents=True)
        (folder / "orders.csv").write_text(ORDERS, encoding="utf-8")
        ingested = self.runner.ingest(IngestRun(upload_id=str(dataset_id)))
        assert ingested.output_dir is not None
        built = self.runner.build(BuildRun(ingest_id=ingested.output_dir.name, spec=SPEC))
        assert built.output_dir is not None
        self.datasets.mkdir(parents=True)
        built.output_dir.rename(self.datasets / str(dataset_id))
        shutil.rmtree(ingested.output_dir)
        datasets.mark_proposed(dataset_id, SPEC.model_dump(mode="json"), {}, [])
        assert datasets.approve(self.workspace, dataset_id, None) == "ready"
        self.runner.builds.clear()
        return dataset_id

    @property
    def folder(self) -> str:
        return f"{self.dataset_id}-v2"

    def add_files(self, new: dict[str, str], keep: tuple[str, ...] = ("orders.csv",)) -> None:
        """What the API does: a folder with kept files linked and new files saved."""
        folder = self.uploads / self.folder
        folder.mkdir()
        files = []
        for name in keep:
            os.link(self.uploads / str(self.dataset_id) / name, folder / name)
            files.append({"name": name, "stored_as": name, "bytes": 10, "added_in": 1})
        for name, text in new.items():
            (folder / name).write_text(text, encoding="utf-8")
            files.append({"name": name, "stored_as": name, "bytes": len(text), "added_in": 2})
        self.store.open_version(self.workspace, self.dataset_id, files, self.folder, 20)

    def handler(self, llm: LLMClient, **options: Any) -> VersionHandler:
        assert POSTGRES_URL is not None
        return VersionHandler(
            url=POSTGRES_URL, schema=self.schema, runner=self.runner, llm=llm,
            datasets_root=self.datasets, uploads_root=self.uploads, **options,
        )  # fmt: skip

    def claim(self) -> Job:
        job = JobQueue(self.conn).claim("w", lease_seconds=30)
        assert job is not None and job.kind == "version"
        return job

    def run(
        self, llm: LLMClient, lost: threading.Event | None = None, **options: Any
    ) -> dict[str, Any]:
        return self.handler(llm, **options)(self.claim(), lost or threading.Event())

    def version(self, number: int = 2) -> VersionRecord:
        record = self.store.get(self.workspace, self.dataset_id, number)
        assert record is not None
        return record

    def current_version(self) -> int:
        return self.store.current(self.dataset_id).version

    def entry(self, table: str) -> dict[str, Any]:
        return next(e for e in self.version().changes if e["table"] == table)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        path = self.datasets / f"{self.dataset_id}-v2" / CLEAN_DATABASE
        with duckdb.connect(str(path), read_only=True) as connection:
            return connection.execute(sql).fetchall()


def fake_llm(*replies: str, ledger: CostLedger | None = None) -> tuple[LLMClient, Scripted]:
    adapter = Scripted(list(replies))
    price = ModelPrice(input_per_million=1000.0, output_per_million=1000.0)
    client = LLMClient(
        [Route(adapter, "m", price)], ledger or CostLedger(), sleep=lambda _: None, max_retries=0
    )
    return client, adapter


@pytest.fixture
def setup(schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path) -> Setup:
    return Setup(schema, conn, workspace, tmp_path)


def test_same_shape_applies_without_model_call(setup: Setup) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n4,80,15/03/2026\n"})
    llm, adapter = fake_llm()

    outcome = setup.run(llm)

    assert adapter.requests == []
    assert outcome["status"] == "applied"
    assert (setup.version().status, setup.current_version()) == ("applied", 2)
    assert (setup.datasets / f"{setup.dataset_id}-v2" / BUILD_REPORT).is_file()
    assert setup.rows("SELECT count(*) FROM orders") == [(4,)]
    assert setup.entry("orders")["kind"] == "grown"
    assert setup.entry("orders")["new_rows"] == 2
    # Version 1 had no stored headers; the check stored them for the next version.
    assert setup.version(1).tables and setup.version().tables
    record = DatasetStore(setup.conn).get(setup.dataset_id, setup.workspace)
    assert record is not None and len(record.files) == 2


def test_resent_file_waits_for_choice(setup: Setup) -> None:
    setup.add_files({"orders_1.csv": ORDERS})
    llm, adapter = fake_llm()

    setup.run(llm)

    version = setup.version()
    assert version.status == "needs_choice"
    assert setup.current_version() == 1
    assert not (setup.datasets / f"{setup.dataset_id}-v2").exists()
    exact = [o for o in setup.entry("orders")["overlaps"] if o["kind"] == "exact"]
    assert exact[0]["rows"] == 2 and len(exact[0]["examples"]) == 2
    assert adapter.requests == []


def test_new_table_proposes_and_keeps_old_spec(setup: Setup) -> None:
    setup.add_files({"customers.csv": "name,city\nAn,Hue\nBinh,Hanoi\n"})
    llm, adapter = fake_llm(CUSTOMERS_REPLY)

    setup.run(llm)

    version = setup.version()
    assert version.status == "proposed"
    assert len(adapter.requests) == 1
    assert version.spec is not None
    assert version.spec["tables"][0] == SPEC.tables[0].model_dump(mode="json")
    assert [t["name"] for t in version.spec["tables"]] == ["orders", "customers"]
    assert setup.entry("customers")["kind"] == "new"
    assert setup.current_version() == 1
    assert not (setup.datasets / f"{setup.dataset_id}-v2").exists()


def test_replace_only_file_removes_table_and_lists_stale_metric(setup: Setup) -> None:
    MemoryStore(setup.conn).save_metric(
        setup.workspace, setup.dataset_id, name="Revenue", aliases=[],
        definition={"table": "orders", "measure": "sum(total)", "filters": []},
        text="Revenue is the sum of totals.", author="u", source_run_id=None,
    )  # fmt: skip
    # The user replaced orders.csv, the only file of `orders`, with a file of another shape.
    setup.add_files({"sales.csv": "id,amount,day\n5,10,01/04/2026\n"}, keep=())
    reply = json.dumps({"tables": [{"source": "sales", "name": "sales"}]})
    llm, _ = fake_llm(reply)

    setup.run(llm)

    assert setup.version().status == "proposed"
    removed = setup.entry("orders")
    assert removed["kind"] == "removed"
    assert removed["stale_metrics"] == ["Revenue"]
    sales = setup.entry("sales")
    assert (sales["kind"], sales["near_match"]) == ("new", "orders")
    assert (sales["missing_columns"], sales["extra_columns"]) == (["total"], ["amount"])
    assert setup.current_version() == 1


def test_quality_drop_proposes_repair(setup: Setup) -> None:
    # Month first: 02/28 is no day-month date, so the approved `dmy` parses none of them.
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,02/28/2026\n4,80,03/31/2026\n"})
    fixed = SPEC.tables[0].model_dump(mode="json")
    fixed["columns"][2]["date_formats"] = ["dmy", "mdy"]
    llm, adapter = fake_llm(json.dumps({"tables": [fixed]}))

    setup.run(llm)

    version = setup.version()
    assert version.status == "proposed"
    assert len(adapter.requests) == 1
    assert [RepairChange.model_validate(r) for r in version.repairs] == [
        RepairChange(
            table="orders", path="columns.day.date_formats", before=["dmy"], after=["dmy", "mdy"]
        )
    ]
    assert setup.entry("orders")["quality"]
    assert version.spec is not None and version.spec["tables"][0]["columns"][2]["date_formats"] == [
        "dmy"
    ]


@pytest.mark.parametrize(
    ("choice", "rows", "files"),
    [
        ("drop_overlap", 3, ["orders.csv", "orders_2.csv"]),
        ("replace", 2, ["orders_2.csv"]),
        ("keep_both", 4, ["orders.csv", "orders_2.csv"]),
    ],
)
def test_build_step_applies_choices(setup: Setup, choice: str, rows: int, files: list[str]) -> None:
    # Row 2 is sent again; row 3 is new.
    setup.add_files({"orders_2.csv": "id,total,day\n2,250,15/02/2026\n3,300,20/02/2026\n"})
    llm, adapter = fake_llm()
    setup.run(llm)
    assert setup.version().status == "needs_choice"
    replace_files = {"orders": "orders.csv"} if choice == "replace" else {}
    choices = {"tables": {"orders": choice}, "replace_files": replace_files, "apply_anyway": False}
    setup.store.approve(setup.workspace, setup.dataset_id, 2, choices, None, [])

    setup.run(llm)

    assert (setup.version().status, setup.current_version()) == ("applied", 2)
    assert setup.rows("SELECT count(*) FROM orders") == [(rows,)]
    assert [f["stored_as"] for f in setup.version().files] == files
    record = DatasetStore(setup.conn).get(setup.dataset_id, setup.workspace)
    assert record is not None and [f["stored_as"] for f in record.files] == files
    if choice == "drop_overlap":
        assert setup.runner.builds[-1].spec.tables[0].drop_overlap == [
            OverlapDrop(file="orders_2.csv", against=["orders.csv"], key=[])
        ]
        excluded = setup.rows("SELECT reason, _source FROM _excluded")
        assert excluded == [("already in orders.csv", "orders_2.csv")]
    if choice == "replace":
        assert not (setup.uploads / setup.folder / "orders.csv").exists()
        assert (setup.uploads / str(setup.dataset_id) / "orders.csv").exists()
    assert adapter.requests == []


@pytest.mark.parametrize(("apply_anyway", "status", "current"), [(False, "failed", 1),
                                                                 (True, "applied", 2)])  # fmt: skip
def test_a_build_that_still_fails_quality_is_applied_only_when_asked(
    setup: Setup, apply_anyway: bool, status: str, current: int
) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,02/28/2026\n4,80,03/31/2026\n"})
    llm, _ = fake_llm("not json", "not json", "not json")
    setup.run(llm)
    assert setup.version().status == "proposed" and setup.version().repairs == []
    choices = {"tables": {}, "replace_files": {}, "apply_anyway": apply_anyway}
    setup.store.approve(setup.workspace, setup.dataset_id, 2, choices, None, [])

    setup.run(llm)

    assert (setup.version().status, setup.current_version()) == (status, current)


def test_build_step_applies_accepted_repairs_and_new_table_edits(setup: Setup) -> None:
    setup.add_files(
        {
            "orders_2.csv": "id,total,day\n3,300,02/28/2026\n4,80,03/31/2026\n",
            "customers.csv": "name,city\nAn,Hue\n",
        }
    )
    fixed = SPEC.tables[0].model_dump(mode="json")
    fixed["columns"][2]["date_formats"] = ["dmy", "mdy"]
    llm, _ = fake_llm(CUSTOMERS_REPLY, json.dumps({"tables": [fixed]}))
    setup.run(llm)
    version = setup.version()
    assert version.status == "proposed" and version.spec is not None
    edited = json.loads(json.dumps(version.spec))
    edited["tables"][1]["name"] = "clients"
    # An edit to an existing table is ignored: code keeps it as approved.
    edited["tables"][0]["description"] = "changed"
    choices = {"tables": {}, "replace_files": {}, "apply_anyway": False}
    setup.store.approve(setup.workspace, setup.dataset_id, 2, choices, edited, version.repairs)

    setup.run(llm)

    assert (setup.version().status, setup.current_version()) == ("applied", 2)
    spec = DatasetSpec.model_validate(setup.version().spec)
    assert [t.name for t in spec.tables] == ["orders", "clients"]
    assert spec.tables[0].description == ""
    assert spec.tables[0].columns[2].date_formats == ["dmy", "mdy"]
    assert setup.rows("SELECT count(*) FROM orders WHERE day IS NOT NULL") == [(4,)]


def test_failure_keeps_current_version(setup: Setup) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})
    setup.runner.fail_ingest.add(setup.folder)
    llm, _ = fake_llm()

    outcome = setup.run(llm)

    version = setup.version()
    assert (version.status, version.error) == ("failed", FAILURES["ingest_failed"])
    assert outcome["status"] == "failed"
    assert setup.current_version() == 1
    assert not (setup.uploads / setup.folder).exists()
    assert (setup.uploads / str(setup.dataset_id) / "orders.csv").exists()
    assert (setup.datasets / str(setup.dataset_id) / BUILD_REPORT).exists()
    assert not list(setup.runner.staging.iterdir())


def test_a_crash_fails_the_version_and_frees_the_slot(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})

    def crash(request: BuildRun) -> BuildResult:
        raise RuntimeError("cannot reach /srv/secret")

    monkeypatch.setattr(setup.runner, "build", crash)
    llm, _ = fake_llm()

    with pytest.raises(RuntimeError):
        setup.run(llm)

    version = setup.version()
    assert (version.status, version.error) == ("failed", FAILURES["version_failed"])
    assert setup.current_version() == 1
    assert not (setup.uploads / setup.folder).exists()


def test_a_drop_choice_on_a_date_only_overlap_fails_the_build(setup: Setup) -> None:
    # New ids, dates inside the old range: only a date overlap, which no row-drop can resolve.
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,05/02/2026\n"})
    llm, _ = fake_llm()
    setup.run(llm)
    entry = setup.entry("orders")
    assert setup.version().status == "needs_choice"
    assert [o["kind"] for o in entry["overlaps"]] == ["date"]
    assert entry["allowed_choices"] == ["replace", "keep_both"]
    choices = {"tables": {"orders": "drop_overlap"}, "replace_files": {}, "apply_anyway": False}
    setup.store.approve(setup.workspace, setup.dataset_id, 2, choices, None, [])

    setup.run(llm)

    version = setup.version()
    assert (version.status, version.error) == ("failed", FAILURES["choices_invalid"])
    assert setup.current_version() == 1


def test_a_worker_that_lost_its_job_changes_nothing(setup: Setup) -> None:
    lost = threading.Event()
    lost.set()
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})
    llm, _ = fake_llm()

    outcome = setup.run(llm, lost=lost)

    assert outcome["status"] == "lost"
    assert setup.version().status == "uploaded" and setup.current_version() == 1
    assert not (setup.datasets / f"{setup.dataset_id}-v2").exists()


@pytest.mark.parametrize("ending", ["needs_choice", "failed", "applied"])
def test_a_lease_lost_mid_step_leaves_the_version_to_the_next_attempt(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    # The lease is lost while the sandbox builds; the late attempt must not change the version.
    new = {"needs_choice": ORDERS, "failed": "", "applied": "id,total,day\n3,300,01/03/2026\n"}
    setup.add_files({"orders_2.csv": new[ending]})
    lost = threading.Event()
    build = setup.runner.build

    def build_then_lose(request: BuildRun) -> BuildResult:
        lost.set()
        result = build(request)
        if ending == "failed":
            return result.model_copy(update={"status": "error", "report": None})
        return result

    monkeypatch.setattr(setup.runner, "build", build_then_lose)
    llm, _ = fake_llm()

    assert setup.run(llm, lost=lost)["status"] == "lost"

    assert setup.version().status == "checking" and setup.current_version() == 1
    assert (setup.uploads / setup.folder).exists()
    assert not (setup.datasets / f"{setup.dataset_id}-v2").exists()


def test_a_version_another_attempt_failed_is_never_applied(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})
    apply = DatasetVersionStore.apply

    def failed_meanwhile(self: DatasetVersionStore, *args: Any, **kwargs: Any) -> bool:
        # The attempt that took over after a lost lease failed the version just before.
        self.mark(setup.dataset_id, 2, "failed", error="the other attempt failed")
        return apply(self, *args, **kwargs)

    monkeypatch.setattr(DatasetVersionStore, "apply", failed_meanwhile)
    llm, _ = fake_llm()

    assert setup.run(llm)["status"] == "failed"

    assert setup.version().status == "failed" and setup.current_version() == 1
    assert not (setup.datasets / f"{setup.dataset_id}-v2").exists()


def test_a_discarded_version_is_not_checked(setup: Setup) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})
    setup.store.discard(setup.workspace, setup.dataset_id, 2)
    llm, _ = fake_llm()

    outcome = setup.run(llm)

    assert outcome["status"] == "discarded"
    assert setup.version().status == "discarded" and setup.current_version() == 1
    assert setup.runner.builds == []


def test_retry_after_install_applies_once(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    setup.add_files({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"})
    llm, _ = fake_llm()
    apply = DatasetVersionStore.apply

    def killed(self: DatasetVersionStore, *args: Any, **kwargs: Any) -> None:
        raise Killed

    monkeypatch.setattr(DatasetVersionStore, "apply", killed)
    with pytest.raises(Killed):
        setup.run(llm)
    target = setup.datasets / f"{setup.dataset_id}-v2"
    assert target.is_dir() and setup.current_version() == 1
    monkeypatch.setattr(DatasetVersionStore, "apply", apply)
    setup.conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")

    setup.run(llm)

    versions = setup.store.list(setup.workspace, setup.dataset_id)
    assert [(v.version, v.status) for v in versions] == [(1, "applied"), (2, "applied")]
    assert setup.current_version() == 2
    assert setup.rows("SELECT count(*) FROM orders") == [(3,)]
    assert sorted(p.name for p in setup.datasets.iterdir()) == sorted(
        [str(setup.dataset_id), target.name]
    )


def test_model_cost_charged_to_job(setup: Setup) -> None:
    assert POSTGRES_URL is not None
    setup.add_files({"customers.csv": "name,city\nAn,Hue\n"})
    budgets = WorkspaceBudgets(url=POSTGRES_URL, schema=setup.schema, daily_usd=5, run_usd=1)
    with connect(POSTGRES_URL, schema=setup.schema) as ledger_conn:
        llm, _ = fake_llm(CUSTOMERS_REPLY, ledger=PostgresCostLedger(ledger_conn))
        job = setup.claim()
        setup.handler(llm, budgets=budgets)(job, threading.Event())

    rows = setup.conn.execute("SELECT run_id, workspace_id FROM llm_calls").fetchall()
    assert rows == [(str(job.id), setup.workspace)]


def test_an_exhausted_budget_fails_a_proposal_but_not_the_same_shape_path(
    setup: Setup,
) -> None:
    assert POSTGRES_URL is not None
    set_daily_budget(setup.conn, setup.workspace, 0.0)
    budgets = WorkspaceBudgets(url=POSTGRES_URL, schema=setup.schema, daily_usd=5, run_usd=1)
    setup.add_files({"customers.csv": "name,city\nAn,Hue\n"})
    llm, adapter = fake_llm(CUSTOMERS_REPLY)

    setup.run(llm, budgets=budgets)

    version = setup.version()
    assert version.status == "failed" and "daily model budget" in (version.error or "")
    assert adapter.requests == []


@pytest.mark.parametrize(
    ("new", "span"),
    [
        ({"orders_2.csv": "id,total,day\n3,300,01/03/2026\n"}, "version.install"),
        ({"customers.csv": "name,city\nAn,Hue\n"}, "version.propose"),
    ],
)
def test_version_job_traces_its_steps(setup: Setup, new: dict[str, str], span: str) -> None:
    exporter = InMemorySpanExporter()
    provider = configure_tracing("http://unused", exporter=exporter)
    setup.add_files(new)
    llm, _ = fake_llm(CUSTOMERS_REPLY)

    setup.run(llm, tracer=provider.get_tracer("t"))
    provider.force_flush()

    names = {s.name for s in exporter.get_finished_spans()}
    assert {"version.ingest", "version.match", "version.build", span} <= names
