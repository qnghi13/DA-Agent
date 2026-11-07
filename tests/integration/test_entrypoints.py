"""Entry points: the admin commands and the worker built from settings. Needs Postgres."""

from pathlib import Path

import psycopg
import pytest

from da_agent import admin
from da_agent.config import Settings
from da_agent.storage.keys import authenticate
from da_agent.worker.__main__ import build_worker
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration


def settings(tmp_path: Path, schema: str, **extra: str) -> Settings:
    assert POSTGRES_URL is not None
    env = {"DA_AGENT_DATABASE_URL": POSTGRES_URL, "DA_AGENT_DATA_DIR": str(tmp_path)} | extra
    return Settings.from_env(env, schema=schema)


def test_admin_creates_a_workspace_with_a_working_key(
    tmp_path: Path, schema: str, conn: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    code = admin.main(
        ["create-workspace", "Shop", "--daily-budget", "1.5"], settings(tmp_path, schema)
    )

    assert code == 0
    printed = dict(line.split(": ", 1) for line in capsys.readouterr().out.strip().splitlines())
    found = authenticate(conn, printed["key"])
    assert found is not None and str(found.workspace_id) == printed["workspace"]
    budget = conn.execute("SELECT daily_budget_usd FROM workspaces").fetchone()
    assert budget == (1.5,)


def test_admin_revokes_a_key(
    tmp_path: Path, schema: str, conn: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    admin.main(["create-workspace", "Shop"], settings(tmp_path, schema))
    key = capsys.readouterr().out.split("key: ")[1].strip()

    assert admin.main(["revoke-key", key], settings(tmp_path, schema)) == 0
    assert authenticate(conn, key) is None
    assert admin.main(["revoke-key", key], settings(tmp_path, schema)) == 1


def test_the_worker_handles_every_job_kind_with_durable_costs(
    tmp_path: Path, schema: str, conn: psycopg.Connection
) -> None:
    env = {"DA_AGENT_ROUTES": "groq:openai/gpt-oss-20b", "GROQ_API_KEY": "test-key"}

    worker = build_worker(settings(tmp_path, schema, **env), env)

    assert set(worker.handlers) == {"run", "ingest", "build"}
    assert (tmp_path / "uploads").is_dir() and (tmp_path / "datasets").is_dir()
    assert type(worker.handlers["run"].llm._ledger).__name__ == "PostgresCostLedger"  # type: ignore[attr-defined]


def test_the_worker_needs_model_routes(
    tmp_path: Path, schema: str, conn: psycopg.Connection
) -> None:
    with pytest.raises(ValueError, match="DA_AGENT_ROUTES"):
        build_worker(settings(tmp_path, schema), {})
