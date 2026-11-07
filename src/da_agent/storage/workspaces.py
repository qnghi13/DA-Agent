"""Workspaces: the unit of isolation. Every dataset, job, and key belongs to one."""

from uuid import UUID

import psycopg

from da_agent.storage.db import returned_id


def create_workspace(conn: psycopg.Connection, name: str) -> UUID:
    return returned_id(
        conn.execute("INSERT INTO workspaces (name) VALUES (%s) RETURNING id", (name,))
    )


def set_daily_budget(conn: psycopg.Connection, workspace_id: UUID, usd: float | None) -> None:
    """Override the default daily model budget for a workspace; None restores the default."""
    conn.execute("UPDATE workspaces SET daily_budget_usd = %s WHERE id = %s", (usd, workspace_id))
