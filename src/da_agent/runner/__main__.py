"""Serve the Runner: python -m da_agent.runner [--host 127.0.0.1] [--port 8100].

The only process that holds the Docker socket (ADR 0024). Settings come from the environment
(da_agent.config.RunnerSettings): no database URL and no provider keys.
"""

import argparse
import os
import sys

import uvicorn

from da_agent.config import ConfigError, RunnerSettings
from da_agent.runner.runner import Runner
from da_agent.runner.service import create_runner_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m da_agent.runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args(argv)
    try:
        settings = RunnerSettings.from_env(os.environ)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    config = settings.runner_config()
    for root in (config.uploads_root, config.staging_root, config.datasets_root):
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
    app = create_runner_app(Runner(config), settings.token)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
