"""Login links and browser sessions in Postgres (ADR 0025).

Times are moved by editing rows, since expiry is decided on the database clock.
"""

from uuid import UUID

import psycopg
import pytest

from da_agent.storage.sessions import (
    create_login_link,
    create_session,
    find_session,
    redeem_login_link,
    revoke_session,
    revoke_sessions,
)
from da_agent.storage.workspaces import create_workspace

pytestmark = pytest.mark.integration


def test_a_link_is_redeemed_once(conn: psycopg.Connection, workspace: UUID) -> None:
    token = create_login_link(conn, role="member", workspace_id=workspace)

    grant = redeem_login_link(conn, token)

    assert grant is not None
    assert (grant.role, grant.workspace_id) == ("member", workspace)
    assert redeem_login_link(conn, token) is None


def test_an_expired_or_unknown_link_is_refused(conn: psycopg.Connection, workspace: UUID) -> None:
    token = create_login_link(conn, role="member", workspace_id=workspace)
    conn.execute("UPDATE login_links SET expires_at = now() - interval '1 second'")

    assert redeem_login_link(conn, token) is None
    assert redeem_login_link(conn, "dal_" + "A" * 43) is None
    assert redeem_login_link(conn, "not a token") is None


def test_only_the_hash_of_a_token_is_stored(conn: psycopg.Connection, workspace: UUID) -> None:
    link = create_login_link(conn, role="member", workspace_id=workspace)
    session, _ = create_session(conn, role="member", workspace_id=workspace)

    dump = str(conn.execute("SELECT * FROM login_links").fetchall())
    dump += str(conn.execute("SELECT * FROM sessions").fetchall())

    assert link.split("_", 1)[1] not in dump
    assert session.split("_", 1)[1] not in dump


def test_roles_match_workspaces(conn: psycopg.Connection, workspace: UUID) -> None:
    with pytest.raises(ValueError, match="operator"):
        create_login_link(conn, role="operator", workspace_id=workspace)
    with pytest.raises(ValueError, match="member"):
        create_login_link(conn, role="member", workspace_id=None)
    with pytest.raises(ValueError, match="role"):
        create_session(conn, role="admin", workspace_id=None)

    token = create_login_link(conn, role="operator", workspace_id=None)
    grant = redeem_login_link(conn, token)
    assert grant is not None
    assert (grant.role, grant.workspace_id) == ("operator", None)


def test_a_session_finds_its_owner(conn: psycopg.Connection, workspace: UUID) -> None:
    token, session_id = create_session(conn, role="member", workspace_id=workspace)

    found = find_session(conn, token)

    assert found is not None
    assert (found.session_id, found.role, found.workspace_id) == (session_id, "member", workspace)
    assert find_session(conn, "das_" + "A" * 43) is None
    assert find_session(conn, "") is None


def test_an_idle_session_expires(conn: psycopg.Connection, workspace: UUID) -> None:
    token, _ = create_session(conn, role="member", workspace_id=workspace)
    conn.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")

    assert find_session(conn, token) is None


def test_use_slides_the_expiry_at_most_hourly(conn: psycopg.Connection, workspace: UUID) -> None:
    token, _ = create_session(conn, role="member", workspace_id=workspace)
    conn.execute(
        "UPDATE sessions SET last_seen_at = now() - interval '2 hours', "
        "expires_at = now() + interval '1 day'"
    )

    assert find_session(conn, token) is not None
    days = conn.execute(
        "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions"
    ).fetchone()
    assert days is not None and days[0] > 29

    # Used again within the hour: no write.
    conn.execute("UPDATE sessions SET expires_at = now() + interval '1 day'")
    assert find_session(conn, token) is not None
    days = conn.execute(
        "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions"
    ).fetchone()
    assert days is not None and days[0] < 2


def test_sliding_never_passes_the_absolute_limit(conn: psycopg.Connection, workspace: UUID) -> None:
    token, _ = create_session(conn, role="member", workspace_id=workspace)
    conn.execute(
        "UPDATE sessions SET created_at = now() - interval '89 days', "
        "last_seen_at = now() - interval '2 hours'"
    )

    assert find_session(conn, token) is not None
    days = conn.execute(
        "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions"
    ).fetchone()
    assert days is not None and days[0] <= 1

    conn.execute("UPDATE sessions SET created_at = now() - interval '91 days'")
    assert find_session(conn, token) is None


def test_revoked_sessions_stop_working(conn: psycopg.Connection, workspace: UUID) -> None:
    other = create_workspace(conn, "other")
    first, first_id = create_session(conn, role="member", workspace_id=workspace)
    second, _ = create_session(conn, role="member", workspace_id=workspace)
    elsewhere, _ = create_session(conn, role="member", workspace_id=other)
    operator, _ = create_session(conn, role="operator", workspace_id=None)

    assert revoke_session(conn, first_id)
    assert find_session(conn, first) is None
    assert find_session(conn, second) is not None

    assert revoke_sessions(conn, workspace_id=workspace) == 1
    assert find_session(conn, second) is None
    assert find_session(conn, elsewhere) is not None
    assert find_session(conn, operator) is not None

    assert revoke_sessions(conn, workspace_id=None) == 1
    assert find_session(conn, operator) is None
    assert find_session(conn, elsewhere) is not None
