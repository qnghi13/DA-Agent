"""Operator commands. There is no sign-up: an operator creates workspaces and keys here.

    python -m da_agent.admin create-workspace "Shop" [--daily-budget 2.0]
    python -m da_agent.admin create-key WORKSPACE_ID
    python -m da_agent.admin revoke-key KEY
    python -m da_agent.admin set-budget WORKSPACE_ID USD|default

Keys are printed once and cannot be shown again.
"""

import argparse
import os
import sys
from uuid import UUID

from da_agent.config import ConfigError, Settings
from da_agent.storage.db import connect, migrate
from da_agent.storage.keys import create_key, revoke_key
from da_agent.storage.workspaces import create_workspace, set_daily_budget


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m da_agent.admin", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    workspace = commands.add_parser("create-workspace", help="create a workspace and its first key")
    workspace.add_argument("name")
    workspace.add_argument("--daily-budget", type=float, help="USD per day; default from settings")
    key = commands.add_parser("create-key", help="add a key to a workspace")
    key.add_argument("workspace_id", type=UUID)
    revoke = commands.add_parser("revoke-key", help="stop a key from working")
    revoke.add_argument("key")
    budget = commands.add_parser("set-budget", help="set a workspace's daily model budget")
    budget.add_argument("workspace_id", type=UUID)
    budget.add_argument("usd", help="a number, or 'default'")
    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = settings or Settings.from_env(os.environ)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    with connect(settings.database_url, schema=settings.schema) as conn:
        migrate(conn)
        if args.command == "create-workspace":
            workspace_id = create_workspace(conn, args.name)
            if args.daily_budget is not None:
                set_daily_budget(conn, workspace_id, args.daily_budget)
            print(f"workspace: {workspace_id}\nkey: {create_key(conn, workspace_id)}")
        elif args.command == "create-key":
            print(f"key: {create_key(conn, args.workspace_id)}")
        elif args.command == "revoke-key":
            if not revoke_key(conn, args.key):
                print("error: no active key matches", file=sys.stderr)
                return 1
            print("revoked")
        elif args.command == "set-budget":
            usd = None if args.usd == "default" else float(args.usd)
            set_daily_budget(conn, args.workspace_id, usd)
            print(f"daily budget: {args.usd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
