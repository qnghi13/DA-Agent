"""Login links and browser sessions (ADR 0025).

A login link carries a one-time token (`dal_...`) that is valid for a few minutes. Redeeming it
creates a session, whose token (`das_...`) lives in an HttpOnly cookie. Both are 256 random bits
and stored only as SHA-256 hashes, so the database never holds a usable token. Expiry is decided
on the database clock: a session ends after `IDLE` without use or `MAX_AGE` after creation, and
use moves the idle deadline forward at most once per `SLIDE_EVERY`, so busy sessions do not
write on every request.
"""

import datetime as dt
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import psycopg

from da_agent.storage.db import returned_id

Role = Literal["member", "operator"]
ROLES = ("member", "operator")
LINK_PATTERN = re.compile(r"dal_[A-Za-z0-9_-]{43}")
SESSION_PATTERN = re.compile(r"das_[A-Za-z0-9_-]{43}")
LINK_TTL = dt.timedelta(minutes=15)
IDLE = dt.timedelta(days=30)
MAX_AGE = dt.timedelta(days=90)
SLIDE_EVERY = dt.timedelta(hours=1)


@dataclass(frozen=True)
class Grant:
    """Who a redeemed link signs in."""

    role: Role
    workspace_id: UUID | None


@dataclass(frozen=True)
class SessionIdentity:
    session_id: UUID
    role: Role
    workspace_id: UUID | None


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _check(role: str, workspace_id: UUID | None) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if role == "operator" and workspace_id is not None:
        raise ValueError("an operator belongs to no workspace")
    if role == "member" and workspace_id is None:
        raise ValueError("a member needs a workspace")


def create_login_link(conn: psycopg.Connection, *, role: str, workspace_id: UUID | None) -> str:
    """Create a one-time login token and return it. It cannot be read back later."""
    _check(role, workspace_id)
    token = f"dal_{secrets.token_urlsafe(32)}"
    conn.execute(
        "INSERT INTO login_links (token_hash, role, workspace_id, expires_at) "
        "VALUES (%s, %s, %s, now() + %s)",
        (_hash(token), role, workspace_id, LINK_TTL),
    )
    return token


def redeem_login_link(conn: psycopg.Connection, token: str) -> Grant | None:
    """Use a link once. Unknown, used, and expired links all return None."""
    if not LINK_PATTERN.fullmatch(token):
        return None
    row = conn.execute(
        "UPDATE login_links SET used_at = now() "
        "WHERE token_hash = %s AND used_at IS NULL AND expires_at > now() "
        "RETURNING role, workspace_id",
        (_hash(token),),
    ).fetchone()
    return Grant(role=row[0], workspace_id=row[1]) if row else None


def create_session(
    conn: psycopg.Connection, *, role: str, workspace_id: UUID | None
) -> tuple[str, UUID]:
    """Create a session and return its token and id."""
    _check(role, workspace_id)
    token = f"das_{secrets.token_urlsafe(32)}"
    session_id = returned_id(
        conn.execute(
            "INSERT INTO sessions (token_hash, role, workspace_id, expires_at) "
            "VALUES (%s, %s, %s, now() + %s) RETURNING id",
            (_hash(token), role, workspace_id, IDLE),
        )
    )
    return token, session_id


def find_session(conn: psycopg.Connection, token: str) -> SessionIdentity | None:
    """The live session for a token, sliding its expiry if it was last moved over an hour ago."""
    if not SESSION_PATTERN.fullmatch(token):
        return None
    token_hash = _hash(token)
    row = conn.execute(
        "UPDATE sessions SET last_seen_at = now(), "
        "expires_at = least(now() + %(idle)s, created_at + %(max_age)s) "
        "WHERE token_hash = %(hash)s AND revoked_at IS NULL AND expires_at > now() "
        "AND created_at > now() - %(max_age)s AND last_seen_at < now() - %(slide)s "
        "RETURNING id, role, workspace_id",
        {"hash": token_hash, "idle": IDLE, "max_age": MAX_AGE, "slide": SLIDE_EVERY},
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, role, workspace_id FROM sessions "
            "WHERE token_hash = %(hash)s AND revoked_at IS NULL AND expires_at > now() "
            "AND created_at > now() - %(max_age)s",
            {"hash": token_hash, "max_age": MAX_AGE},
        ).fetchone()
    if row is None:
        return None
    return SessionIdentity(session_id=row[0], role=row[1], workspace_id=row[2])


def revoke_session(conn: psycopg.Connection, session_id: UUID) -> bool:
    cursor = conn.execute(
        "UPDATE sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
        (session_id,),
    )
    return cursor.rowcount == 1


def revoke_sessions(conn: psycopg.Connection, *, workspace_id: UUID | None) -> int:
    """Revoke every live session of a workspace, or of the operator when `workspace_id` is None."""
    cursor = conn.execute(
        "UPDATE sessions SET revoked_at = now() "
        "WHERE workspace_id IS NOT DISTINCT FROM %s AND revoked_at IS NULL",
        (workspace_id,),
    )
    return cursor.rowcount
