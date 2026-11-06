"""Sandbox entrypoint. Reads one JSON request on stdin and runs it.

This code runs inside the container, so it trusts nothing it receives and holds nothing worth
stealing. The container, not this file, is the security boundary.
"""

import json
import signal
import sys
import traceback
from pathlib import Path

APP = "/app"


def run_python(code: str) -> int:
    try:
        # Running untrusted code is this container's job. The container flags contain it.
        exec(compile(code, "<run>", "exec"), {"__name__": "__main__"})  # noqa: S102
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


def run_ingest() -> int:
    # -I leaves /app off sys.path. It is read-only, so adding it does not let code plant modules.
    sys.path.insert(0, APP)
    from da_agent.ingestion.raw import ingest

    report = ingest(Path("/input"), Path("/out"))
    Path("/out/report.json").write_text(report.model_dump_json(), encoding="utf-8")
    return 0


def run_build(request: dict) -> int:
    sys.path.insert(0, APP)
    from da_agent.ingestion.build import build
    from da_agent.ingestion.report import IngestReport
    from da_agent.ingestion.spec import DatasetSpec

    report = IngestReport.model_validate_json(Path("/input/report.json").read_text("utf-8"))
    spec = DatasetSpec.model_validate(request["spec"])
    result = build(Path("/input/raw.duckdb"), spec, report, Path("/out"))
    Path("/out/build_report.json").write_text(result.model_dump_json(), encoding="utf-8")
    return 0


def run_sql(request: dict) -> int:
    sys.path.insert(0, APP)
    from da_agent.storage.duckdb_query import run_query

    result = run_query(
        Path("/data/dataset.duckdb"), str(request["sql"]), max_rows=int(request["max_rows"])
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    request = json.loads(sys.stdin.read())
    # The default SIGALRM action ends the process, and user code cannot catch it as an exception.
    signal.alarm(int(request["timeout_seconds"]))
    kind = request.get("kind")
    if kind == "python":
        return run_python(request["code"])
    if kind == "sql":
        return run_sql(request)
    if kind == "ingest":
        return run_ingest()
    if kind == "build":
        return run_build(request)
    print(f"unknown request kind {kind!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.exit(code)
