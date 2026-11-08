"""Browser sign-in over HTTP: login links, cookie sessions, Origin checks, and roles (ADR 0025).

Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.api.app import ApiSettings, create_app
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.sessions import create_login_link, revoke_sessions
from da_agent.storage.workspaces import create_workspace
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration

ORIGIN = "http://testserver"
SAME = {"Origin": ORIGIN}


@pytest.fixture
def settings(schema: str, tmp_path: Path) -> ApiSettings:
    assert POSTGRES_URL is not None
    return ApiSettings(
        database_url=POSTGRES_URL,
        schema=schema,
        uploads_root=tmp_path / "uploads",
        public_url=ORIGIN,
        failed_auth_per_minute=3,
    )


@pytest.fixture
def client(settings: ApiSettings) -> TestClient:
    return TestClient(create_app(settings))


def sign_in(client: TestClient, conn: psycopg.Connection, workspace: UUID | None) -> None:
    role = "operator" if workspace is None else "member"
    token = create_login_link(conn, role=role, workspace_id=workspace)
    response = client.post("/v1/auth/redeem", json={"token": token}, headers=SAME)
    assert response.status_code == 200, response.text


def audit(conn: psycopg.Connection) -> list[tuple[str, bool]]:
    rows = conn.execute("SELECT action, session_id IS NOT NULL FROM audit_log ORDER BY id")
    return [(row[0], row[1]) for row in rows]


def test_a_link_signs_a_browser_in_with_a_cookie(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    token = create_login_link(conn, role="member", workspace_id=workspace)

    response = client.post("/v1/auth/redeem", json={"token": token}, headers=SAME)

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("da_session=das_")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/;" in cookie
    assert "secure" not in cookie  # the public URL is plain http
    me = client.get("/v1/auth/me").json()
    assert me == {"role": "member", "workspace_id": str(workspace), "workspace_name": "shop"}
    assert client.get("/v1/datasets").status_code == 200


def test_a_link_works_only_once(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    token = create_login_link(conn, role="member", workspace_id=workspace)
    assert client.post("/v1/auth/redeem", json={"token": token}, headers=SAME).status_code == 200

    again = TestClient(client.app).post("/v1/auth/redeem", json={"token": token}, headers=SAME)

    assert again.status_code == 401
    assert "set-cookie" not in again.headers


@pytest.mark.security
def test_guessed_links_are_logged_and_slowed(client: TestClient, conn: psycopg.Connection) -> None:
    codes = [
        client.post("/v1/auth/redeem", json={"token": "dal_" + "A" * 43}, headers=SAME).status_code
        for _ in range(4)
    ]

    assert codes == [401, 401, 401, 429]
    assert [action for action, _ in audit(conn)].count("auth.failed") == 3


def test_https_deployments_get_a_secure_host_only_cookie(
    settings: ApiSettings, conn: psycopg.Connection, workspace: UUID
) -> None:
    secure = TestClient(
        create_app(ApiSettings(**{**settings.__dict__, "public_url": "https://da.example"})),
        base_url="https://da.example",
    )
    token = create_login_link(conn, role="member", workspace_id=workspace)

    response = secure.post(
        "/v1/auth/redeem", json={"token": token}, headers={"Origin": "https://da.example"}
    )

    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("__host-da_session=das_")
    assert "secure" in cookie and "path=/;" in cookie
    assert secure.get("/v1/auth/me").json()["role"] == "member"


def test_a_key_can_be_exchanged_for_a_session(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key = create_key(conn, workspace)

    response = client.post("/v1/auth/session", headers={**SAME, "Authorization": f"Bearer {key}"})

    assert response.status_code == 200
    assert "da_session=das_" in response.headers["set-cookie"]
    assert client.get("/v1/auth/me").json()["workspace_id"] == str(workspace)
    assert TestClient(client.app).post("/v1/auth/session", headers=SAME).status_code == 401


@pytest.mark.security
def test_cookie_requests_that_change_things_need_our_origin(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)
    upload = {"data": {"name": "x"}, "files": [("files", ("a.csv", b"a\n1\n"))]}

    assert client.post("/v1/datasets", **upload).status_code == 403
    assert (
        client.post(
            "/v1/datasets", headers={"Origin": "https://evil.example"}, **upload
        ).status_code
        == 403
    )
    assert client.post("/v1/datasets", headers=SAME, **upload).status_code == 202
    # Reading needs no Origin: SameSite=Strict keeps the cookie off cross-site requests.
    assert client.get("/v1/datasets").status_code == 200


@pytest.mark.security
def test_a_foreign_page_cannot_sign_a_browser_in(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    token = create_login_link(conn, role="member", workspace_id=workspace)

    response = client.post(
        "/v1/auth/redeem", json={"token": token}, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    # The link was not spent by the refused request.
    assert client.post("/v1/auth/redeem", json={"token": token}, headers=SAME).status_code == 200


def test_logout_ends_the_session(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)
    cookies = dict(client.cookies)

    response = client.post("/v1/auth/logout", headers=SAME)

    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    stale = TestClient(client.app, cookies=cookies)
    assert stale.get("/v1/datasets").status_code == 401


def test_revoked_sessions_are_refused(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)

    revoke_sessions(conn, workspace_id=workspace)

    assert client.get("/v1/datasets").status_code == 401


@pytest.mark.security
def test_a_session_sees_only_its_workspace(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    other = create_workspace(conn, "other")
    other_key = create_key(conn, other)
    files = [("files", ("a.csv", b"a\n1\n"))]
    theirs = client.post(
        "/v1/datasets",
        headers={"Authorization": f"Bearer {other_key}"},
        data={"name": "theirs"},
        files=files,
    ).json()["id"]

    sign_in(client, conn, workspace)

    assert client.get(f"/v1/datasets/{theirs}").status_code == 404
    assert client.get("/v1/datasets").json() == {"datasets": []}


@pytest.mark.security
def test_the_operator_cannot_use_workspace_endpoints(
    client: TestClient, conn: psycopg.Connection
) -> None:
    sign_in(client, conn, None)

    assert client.get("/v1/auth/me").json() == {
        "role": "operator",
        "workspace_id": None,
        "workspace_name": None,
    }
    assert client.get("/v1/datasets").status_code == 404
    assert (
        client.post("/v1/runs", json={"dataset_id": "x", "question": "q"}, headers=SAME).status_code
        == 404
    )


def test_signed_in_members_invite_into_their_own_workspace(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)

    response = client.post("/v1/auth/links", json={}, headers=SAME)

    assert response.status_code == 201
    url = response.json()["url"]
    assert url.startswith(f"{ORIGIN}/login#token=dal_")
    assert response.json()["expires_in_seconds"] == 900
    invited = TestClient(client.app)
    token = url.split("#token=", 1)[1]
    assert invited.post("/v1/auth/redeem", json={"token": token}, headers=SAME).json() == {
        "role": "member",
        "workspace_id": str(workspace),
        "workspace_name": "shop",
    }
    assert ("auth.link", True) in audit(conn)
    assert ("auth.sign_in", True) in audit(conn)


@pytest.mark.security
def test_members_cannot_make_operator_links(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)
    key = create_key(conn, workspace)

    assert client.post("/v1/auth/links", json={"role": "operator"}, headers=SAME).status_code == 404
    assert (
        TestClient(client.app)
        .post(
            "/v1/auth/links", json={"role": "operator"}, headers={"Authorization": f"Bearer {key}"}
        )
        .status_code
        == 404
    )


def test_the_operator_invites_operators(client: TestClient, conn: psycopg.Connection) -> None:
    sign_in(client, conn, None)

    response = client.post("/v1/auth/links", json={"role": "operator"}, headers=SAME)

    assert response.status_code == 201
    token = response.json()["url"].split("#token=", 1)[1]
    invited = TestClient(client.app)
    assert (
        invited.post("/v1/auth/redeem", json={"token": token}, headers=SAME).json()["role"]
        == "operator"
    )


def test_session_actions_are_audited_with_the_session(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    sign_in(client, conn, workspace)

    client.post(
        "/v1/datasets",
        headers=SAME,
        data={"name": "x"},
        files=[("files", ("a.csv", b"a\n1\n"))],
    )

    assert ("dataset.upload", True) in audit(conn)


# Operator dashboard (ADR 0026)


@pytest.mark.security
def test_only_the_operator_reads_the_dashboard(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key = create_key(conn, workspace)
    assert client.get("/v1/ops/summary").status_code == 401
    keyed = client.get("/v1/ops/summary", headers={"Authorization": f"Bearer {key}"})
    assert keyed.status_code == 404
    sign_in(client, conn, workspace)
    assert client.get("/v1/ops/summary").status_code == 404
    assert client.get("/v1/ops/failures").status_code == 404

    operator = TestClient(client.app)
    sign_in(operator, conn, None)
    response = operator.get("/v1/ops/summary", params={"window": "7d"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["window"] == "7d"
    assert operator.get("/v1/ops/summary", params={"window": "1y"}).status_code == 422


def test_the_dashboard_fills_in_default_budgets_and_trace_links(
    settings: ApiSettings, conn: psycopg.Connection, workspace: UUID
) -> None:
    traced = ApiSettings(**{**settings.__dict__, "trace_ui_url": "http://phoenix:6006"})
    operator = TestClient(create_app(traced))
    sign_in(operator, conn, None)
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    conn.execute(
        "UPDATE jobs SET status = 'failed', last_error = 'boom', trace_ids = '{t1}' WHERE id = %s",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO llm_calls (run_id, workspace_id, provider, model, input_tokens, "
        "output_tokens, cached_input_tokens, cost_usd, latency_seconds) "
        "VALUES (%s, %s, 'groq', 'm', 1, 1, 0, 0.5, 1)",
        (str(job_id), workspace),
    )

    today = operator.get("/v1/ops/summary").json()["cost"]["today"]
    failed = operator.get("/v1/ops/failures").json()["failures"]

    assert today[0]["budget_usd"] == traced.daily_budget_usd
    assert failed[0]["traces"] == [{"attempt": 1, "url": "http://phoenix:6006/redirects/traces/t1"}]
    assert "trace_ids" not in failed[0]
