"""Entry points: the admin commands and the worker built from settings. Needs Postgres."""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from da_agent import admin
from da_agent.config import Settings
from da_agent.storage.keys import authenticate
from da_agent.storage.sessions import create_session, find_session, redeem_login_link
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


def test_admin_prints_login_links_for_members_and_the_operator(
    tmp_path: Path, schema: str, conn: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"DA_AGENT_PUBLIC_URL": "https://da.example"}
    admin.main(["create-workspace", "Shop"], settings(tmp_path, schema))
    workspace = capsys.readouterr().out.split("workspace: ")[1].split()[0]

    assert (
        admin.main(["login-link", "--workspace", workspace], settings(tmp_path, schema, **env)) == 0
    )
    member = capsys.readouterr().out.strip()
    assert admin.main(["login-link", "--operator"], settings(tmp_path, schema, **env)) == 0
    operator = capsys.readouterr().out.strip()

    assert member.startswith("https://da.example/login#token=dal_")
    member_grant = redeem_login_link(conn, member.split("#token=")[1])
    operator_grant = redeem_login_link(conn, operator.split("#token=")[1])
    assert member_grant is not None and str(member_grant.workspace_id) == workspace
    assert operator_grant is not None and operator_grant.role == "operator"


def test_admin_link_needs_a_known_workspace(
    tmp_path: Path, schema: str, conn: psycopg.Connection
) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"

    assert admin.main(["login-link", "--workspace", unknown], settings(tmp_path, schema)) == 1


def test_admin_revokes_sessions(
    tmp_path: Path, schema: str, conn: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    admin.main(["create-workspace", "Shop"], settings(tmp_path, schema))
    workspace = capsys.readouterr().out.split("workspace: ")[1].split()[0]
    member, _ = create_session(conn, role="member", workspace_id=UUID(workspace))
    operator, _ = create_session(conn, role="operator", workspace_id=None)

    admin.main(["revoke-sessions", "--workspace", workspace], settings(tmp_path, schema))
    assert capsys.readouterr().out.strip() == "revoked 1 session(s)"
    assert find_session(conn, member) is None
    assert find_session(conn, operator) is not None

    admin.main(["revoke-sessions", "--operator"], settings(tmp_path, schema))
    assert find_session(conn, operator) is None


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


ROUTES = {"DA_AGENT_ROUTES": "groq:openai/gpt-oss-20b", "GROQ_API_KEY": "test-key"}


def test_with_a_runner_url_the_worker_never_starts_sandboxes_itself(
    tmp_path: Path, schema: str, conn: psycopg.Connection
) -> None:
    env = ROUTES | {"DA_AGENT_RUNNER_URL": "http://runner:8100", "DA_AGENT_RUNNER_TOKEN": "k" * 32}

    worker = build_worker(settings(tmp_path, schema, **env), env)

    handlers = worker.handlers.values()
    runners = {type(h.runner).__name__ for h in handlers if hasattr(h, "runner")}
    datasets = worker.handlers["run"].datasets  # type: ignore[attr-defined]
    assert runners == {"RunnerClient"}
    assert type(datasets.runner).__name__ == "RunnerClient"


def test_without_a_runner_url_sandboxes_run_as_the_configured_user(
    tmp_path: Path, schema: str, conn: psycopg.Connection
) -> None:
    env = ROUTES | {"DA_AGENT_SANDBOX_USER": "10001:10001"}

    worker = build_worker(settings(tmp_path, schema, **env), env)

    assert worker.handlers["build"].runner.config.user == "10001:10001"  # type: ignore[attr-defined]
