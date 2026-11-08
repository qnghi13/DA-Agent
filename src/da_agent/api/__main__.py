"""Serve the API: python -m da_agent.api [--host 127.0.0.1] [--port 8000].

Settings come from the environment (da_agent.config). The API never needs provider keys.
"""

import argparse
import os
import sys

import uvicorn

from da_agent.api.app import ApiSettings, create_app
from da_agent.config import ConfigError, Settings
from da_agent.storage.db import connect, migrate


def api_settings(settings: Settings) -> ApiSettings:
    return ApiSettings(
        database_url=settings.database_url,
        uploads_root=settings.uploads_root,
        schema=settings.schema,
        daily_budget_usd=settings.daily_budget_usd,
        trace_ui_url=settings.trace_ui_url,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m da_agent.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    settings.uploads_root.mkdir(parents=True, exist_ok=True)
    with connect(settings.database_url) as conn:
        migrate(conn)
    uvicorn.run(create_app(api_settings(settings)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
