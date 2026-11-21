"""Serve the API: python -m da_agent.api [--host 127.0.0.1] [--port 8000].

Settings come from the environment (da_agent.config). The API never needs provider keys.
"""

import argparse
import os
import sys

import uvicorn
from prometheus_client import REGISTRY

from da_agent.api.app import ApiSettings, create_app
from da_agent.config import ConfigError, Settings
from da_agent.storage.db import connect, migrate
from da_agent.telemetry.db_metrics import DatabaseCollector, connect_db_metrics
from da_agent.telemetry.metrics import api_metrics, serve


def api_settings(settings: Settings) -> ApiSettings:
    return ApiSettings(
        database_url=settings.database_url,
        uploads_root=settings.uploads_root,
        schema=settings.schema,
        daily_budget_usd=settings.daily_budget_usd,
        trace_ui_url=settings.trace_ui_url,
        public_url=settings.public_url,
        exports_root=settings.exports_root,
        alertmanager_url=settings.alertmanager_url,
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
    metrics = None
    if settings.metrics_port is not None:
        # Prometheus scrapes this port on the internal network only (ADR 0044). The API also
        # reports what lives in Postgres: the queue, budgets, and recent ratings.
        metrics = api_metrics()
        REGISTRY.register(
            DatabaseCollector(
                lambda: connect_db_metrics(settings.database_url, settings.schema),
                default_daily_usd=settings.daily_budget_usd,
            )
        )
        serve(settings.metrics_port)
    uvicorn.run(create_app(api_settings(settings), metrics), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
