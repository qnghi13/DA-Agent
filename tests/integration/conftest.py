"""Postgres fixtures: each test gets its own schema, migrated, and dropped afterwards.

Tests that use them are skipped unless DA_AGENT_TEST_POSTGRES_URL is set.
"""

import os
import uuid
from collections.abc import Iterator
from uuid import UUID

import psycopg
import pytest

from da_agent.storage.db import connect, migrate
from da_agent.storage.workspaces import create_workspace

POSTGRES_URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL")


@pytest.fixture
def schema() -> Iterator[str]:
    if not POSTGRES_URL:
        pytest.skip("DA_AGENT_TEST_POSTGRES_URL is not set")
    name = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {name}")
        try:
            yield name
        finally:
            admin.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def conn(schema: str) -> Iterator[psycopg.Connection]:
    assert POSTGRES_URL is not None
    with connect(POSTGRES_URL, schema=schema) as connection:
        migrate(connection)
        yield connection


@pytest.fixture
def workspace(conn: psycopg.Connection) -> UUID:
    return create_workspace(conn, "shop")
