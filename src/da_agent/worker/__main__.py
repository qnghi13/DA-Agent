"""Run a worker: python -m da_agent.worker. Settings come from the environment (da_agent.config).

The worker claims ingest, build, and run jobs, runs sandboxes through the Runner, and records every
model call in Postgres. SIGTERM or Ctrl-C stops it after the current job; if it is killed instead,
the job's lease runs out and another worker continues it.
"""

import os
import signal
import sys
import threading
from collections.abc import Mapping
from typing import Any

import psycopg

from da_agent.config import ConfigError, Settings
from da_agent.llm.client import LLMClient
from da_agent.llm.factory import build_routes
from da_agent.runner.runner import Runner, RunnerConfig
from da_agent.storage.db import connect, migrate
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.telemetry.tracing import configure_tracing
from da_agent.worker.datasets import BuildHandler, DatasetOpener, IngestHandler
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.runs import RunHandler
from da_agent.worker.worker import Worker, WorkerConfig


def build_worker(
    settings: Settings, env: Mapping[str, str], ledger_conn: psycopg.Connection | None = None
) -> Worker:
    """A worker with a handler for every job kind. ledger_conn holds the cost ledger; one is
    opened when it is not given."""
    if not settings.routes:
        raise ValueError("DA_AGENT_ROUTES is required for the worker, such as groq:MODEL")
    for root in (settings.uploads_root, settings.staging_root, settings.datasets_root):
        root.mkdir(parents=True, exist_ok=True)
    runner = Runner(
        RunnerConfig(
            image=settings.sandbox_image,
            datasets_root=settings.datasets_root,
            uploads_root=settings.uploads_root,
            staging_root=settings.staging_root,
        )
    )
    conn = ledger_conn or connect(settings.database_url, schema=settings.schema)
    llm = LLMClient(build_routes(settings.routes, env), PostgresCostLedger(conn))
    database: dict[str, Any] = {"url": settings.database_url, "schema": settings.schema}
    budgets = WorkspaceBudgets(
        **database, daily_usd=settings.daily_budget_usd, run_usd=settings.run_budget_usd,
        run_tokens=settings.run_max_tokens,
    )  # fmt: skip
    roots = {"runner": runner, "datasets_root": settings.datasets_root}
    opener = DatasetOpener(**database, **roots)
    handlers: dict[str, Any] = {
        "run": RunHandler(
            llm=llm,
            datasets=opener,
            checkpoint_url=settings.database_url,
            schema=settings.schema,
            budgets=budgets,
        ),
        "ingest": IngestHandler(**database, **roots, llm=llm, budgets=budgets),
        "build": BuildHandler(**database, **roots),
    }
    return Worker(WorkerConfig(url=settings.database_url, schema=settings.schema), handlers)


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    tracing = None
    if settings.otlp_endpoint:
        tracing = configure_tracing(
            settings.otlp_endpoint, service_name="da-agent-worker", set_global=True
        )
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        with connect(settings.database_url) as conn:
            migrate(conn)
            worker = build_worker(settings, os.environ, ledger_conn=conn)
            print(f"worker {worker.worker_id} waiting for jobs", flush=True)
            worker.run_forever(stop)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    finally:
        if tracing is not None:
            tracing.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
