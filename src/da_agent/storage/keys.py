"""Workspace API keys: `dak_<prefix>_<secret>`, shown once and stored as a SHA-256 hash.

The prefix finds the row, and the hash of the whole key is compared in constant time. A key has
256 random bits, so a fast hash is enough: slow password hashes exist for guessable passwords.
See docs/adr/0020.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

import psycopg

KEY_PATTERN = re.compile(r"dak_([0-9a-f]{12})_[A-Za-z0-9_-]{43}")


@dataclass(frozen=True)
class KeyIdentity:
    key_id: UUID
    workspace_id: UUID


def _hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def create_key(conn: psycopg.Connection, workspace_id: UUID) -> str:
    """Create a key and return it. It cannot be read back later."""
    prefix = secrets.token_hex(6)
    key = f"dak_{prefix}_{secrets.token_urlsafe(32)}"
    conn.execute(
        "INSERT INTO api_keys (workspace_id, prefix, key_hash) VALUES (%s, %s, %s)",
        (workspace_id, prefix, _hash(key)),
    )
    return key


def authenticate(conn: psycopg.Connection, key: str) -> KeyIdentity | None:
    match = KEY_PATTERN.fullmatch(key)
    if match is None:
        return None
    row = conn.execute(
        "SELECT id, workspace_id, key_hash FROM api_keys WHERE prefix = %s AND revoked_at IS NULL",
        (match.group(1),),
    ).fetchone()
    # Compare even when no row matched, so timing does not tell whether a prefix exists.
    stored = bytes(row[2]) if row else b"\0" * 32
    if not hmac.compare_digest(stored, _hash(key)) or row is None:
        return None
    return KeyIdentity(key_id=row[0], workspace_id=row[1])


def revoke_key(conn: psycopg.Connection, key: str) -> bool:
    match = KEY_PATTERN.fullmatch(key)
    if match is None:
        return False
    cursor = conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE prefix = %s AND revoked_at IS NULL",
        (match.group(1),),
    )
    return cursor.rowcount == 1
