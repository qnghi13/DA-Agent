"""Workspaces: the unit of isolation. Every dataset, job, and key belongs to one."""

from uuid import UUID

import psycopg

from da_agent.storage.db import returned_id


def create_workspace(conn: psycopg.Connection, name: str) -> UUID:
    return returned_id(
        conn.execute("INSERT INTO workspaces (name) VALUES (%s) RETURNING id", (name,))
    )
