"""Rerunning golden cases against current code (ADR 0043).

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from da_agent.agent.loop_types import Document
from da_agent.goldens import main, rerun_cases
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.storage.feedback import golden_cases, set_feedback
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.sqlite_dataset import SqliteDataset
from da_agent.worker.runs import RunHandler
from da_agent.worker.worker import PermanentJobError
from tests.integration.conftest import POSTGRES_URL
from tests.unit.test_agent_graph import ScriptedAdapter, plan, sql, submit
from tests.unit.test_agent_graph import dataset as dataset

pytestmark = pytest.mark.integration

SHOP = UUID(int=1)
GONE = UUID(int=2)


def liked_run(conn: psycopg.Connection, workspace: UUID, dataset_id: UUID, value: Any) -> Job:
    queue = JobQueue(conn)
    payload = {"question": "Total revenue?", "as_of": "2026-06-28", "dataset_id": str(dataset_id)}
    job_id = queue.enqueue(workspace, "run", payload)
    assert queue.claim("w", lease_seconds=30) is not None
    answer = {"kind": "number", "value": value, "reason": None}
    queue.complete(job_id, "w", {"stop_reason": "answered", "answer": answer})
    job = queue.get(job_id, workspace)
    assert job is not None
    set_feedback(conn, job, "up", [], None, "member")
    return job


def handler(adapter: ScriptedAdapter, data: SqliteDataset, schema: str) -> RunHandler:
    def datasets(workspace_id: UUID, dataset_id: str):
        if UUID(dataset_id) == GONE:
            raise PermanentJobError("the dataset was deleted")
        return data, [Document("rules", "Revenue is the sum of totals.")]

    llm = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    assert POSTGRES_URL is not None
    return RunHandler(llm=llm, datasets=datasets, checkpoint_url=POSTGRES_URL, schema=schema)


def test_a_rerun_reports_same_changed_and_errors_and_carries_on(
    conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset, schema: str
) -> None:
    liked_run(conn, workspace, SHOP, 350)
    liked_run(conn, workspace, GONE, 350)
    liked_run(conn, workspace, SHOP, 999)
    jobs_before = conn.execute("SELECT count(*) FROM jobs").fetchone()
    adapter = ScriptedAdapter(
        [plan(), sql(), submit("number", 350), plan(), sql(), submit("number", 350)]
    )

    rows = rerun_cases(conn, golden_cases(conn, workspace), handler(adapter, dataset, schema))

    assert [row["outcome"] for row in rows] == ["same", "error", "changed"]
    assert "deleted" in rows[1]["error"]
    assert all(row["run_id"].startswith("golden-") for row in rows)
    assert rows[2]["expected"]["value"] == 999 and rows[2]["actual"]["value"] == 350
    assert conn.execute("SELECT count(*) FROM jobs").fetchone() == jobs_before


def test_list_shows_cases_and_disliked_runs(
    conn: psycopg.Connection, workspace: UUID, schema: str, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    liked = liked_run(conn, workspace, SHOP, 350)
    disliked = liked_run(conn, workspace, SHOP, 1)
    set_feedback(conn, disliked, "down", ["wrong_number"], "June is missing", "member")
    assert POSTGRES_URL is not None
    monkeypatch.setenv(
        "DA_AGENT_DATABASE_URL", make_conninfo(POSTGRES_URL, options=f"-c search_path={schema}")
    )
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))

    assert main(["list", "--workspace", str(workspace), "--json"]) == 0

    shown = json.loads(capsys.readouterr().out)
    assert [case["source_run_id"] for case in shown["cases"]] == [str(liked.id)]
    assert shown["disliked"][0]["reasons"] == ["wrong_number"]
    assert shown["disliked"][0]["note"] == "June is missing"


def test_run_without_yes_only_counts_and_spends_nothing(
    conn: psycopg.Connection, workspace: UUID, schema: str, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    liked_run(conn, workspace, SHOP, 350)
    assert POSTGRES_URL is not None
    monkeypatch.setenv(
        "DA_AGENT_DATABASE_URL", make_conninfo(POSTGRES_URL, options=f"-c search_path={schema}")
    )
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))

    def no_handler() -> RunHandler:
        raise AssertionError("no run handler without --yes")

    assert main(["run", "--workspace", str(workspace)], handler_factory=no_handler) == 0

    assert "1 golden case" in capsys.readouterr().out


def test_a_rerun_writes_each_case_to_a_report_outside_the_repository(
    conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset, schema: str,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    liked_run(conn, workspace, SHOP, 350)
    assert POSTGRES_URL is not None
    url = make_conninfo(POSTGRES_URL, options=f"-c search_path={schema}")
    monkeypatch.setenv("DA_AGENT_DATABASE_URL", url)
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])

    code = main(
        ["run", "--workspace", str(workspace), "--yes"],
        handler_factory=lambda: handler(adapter, dataset, schema),
    )

    assert code == 0
    (report,) = (tmp_path / "data" / "goldens").glob("goldens-*.jsonl")
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert [row["outcome"] for row in rows] == ["same"]
    assert list(tmp_path.glob("goldens-*")) == []
    out = capsys.readouterr().out
    assert ["same", "Total", "revenue?"] in [line.split() for line in out.splitlines()]
    assert "same 1" in out


def test_a_report_folder_that_cannot_be_written_stops_before_spending(
    conn: psycopg.Connection, workspace: UUID, schema: str, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    liked_run(conn, workspace, SHOP, 350)
    assert POSTGRES_URL is not None
    url = make_conninfo(POSTGRES_URL, options=f"-c search_path={schema}")
    monkeypatch.setenv("DA_AGENT_DATABASE_URL", url)
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))

    def no_handler() -> RunHandler:
        raise AssertionError("nothing may run without a report to write")

    missing = tmp_path / "missing" / "report.jsonl"
    args = ["run", "--workspace", str(workspace), "--yes", "--output", str(missing)]
    assert main(args, handler_factory=no_handler) == 1
