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


def run_ingest(request: dict) -> int:
    # -I leaves /app off sys.path. It is read-only, so adding it does not let code plant modules.
    sys.path.insert(0, APP)
    from da_agent.ingestion.layout_spec import LayoutSpec
    from da_agent.ingestion.raw import ingest

    layout = request.get("layout")
    spec = LayoutSpec.model_validate(layout) if layout else None
    report = ingest(Path("/input"), Path("/out"), layout=spec)
    Path("/out/report.json").write_text(report.model_dump_json(), encoding="utf-8")
    return 0


def run_build(request: dict) -> int:
    sys.path.insert(0, APP)
    from da_agent.ingestion.build import build
    from da_agent.ingestion.report import IngestReport
    from da_agent.ingestion.spec import DatasetSpec

    report = IngestReport.model_validate_json(Path("/input/report.json").read_text("utf-8"))
    spec = DatasetSpec.model_validate(request["spec"])
    # In upload order: each new file is checked against the new files before it.
    new_sources = [str(name) for name in request.get("new_sources", [])]
    result = build(Path("/input/raw.duckdb"), spec, report, Path("/out"), new_sources)
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


def run_stats(request: dict) -> int:
    """One fixed analysis (ADR 0051). A problem with the analysis comes back as a message for
    the model, with its kind; anything else is a crash the Runner reports without detail."""
    sys.path.insert(0, APP)
    import datetime as dt

    from da_agent.storage.stats import StatsError
    from da_agent.storage.stats import run_stats as compute

    def day(value: object) -> dt.date | None:
        return dt.date.fromisoformat(str(value)) if value else None

    def span(value: object) -> tuple[dt.date, dt.date] | None:
        if not isinstance(value, dict):
            return None
        start, end = day(value.get("start")), day(value.get("end"))
        return (start, end) if start and end else None

    try:
        result = compute(
            Path("/data/dataset.duckdb"),
            str(request["analysis"]),
            str(request["sql"]),
            str(request["x"]),
            str(request["y"]),
            data_start=day(request.get("data_start")),
            data_end=day(request.get("data_end")),
            periods=int(request.get("periods", 1)),
            dimensions=[str(name) for name in request.get("dimensions", [])],
            before=span(request.get("before")),
            after=span(request.get("after")),
        )
    except StatsError as error:
        result = {"error": str(error), "error_kind": error.kind}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


def run_export(request: dict) -> int:
    """Write the whole result to /out/result.csv. A cap or query error goes into the report, in
    words for the user, and leaves no CSV."""
    sys.path.insert(0, APP)
    import duckdb

    from da_agent.storage.csv_export import ExportLimitError, ExportReport, write_csv

    try:
        report = write_csv(
            Path("/data/dataset.duckdb"), str(request["sql"]), Path("/out/result.csv")
        )
    except (ExportLimitError, duckdb.Error) as error:
        report = ExportReport(rows=0, bytes=0, columns=[], error=str(error)[:500])
    Path("/out/export_report.json").write_text(report.model_dump_json(), encoding="utf-8")
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
        return run_ingest(request)
    if kind == "build":
        return run_build(request)
    if kind == "stats":
        return run_stats(request)
    if kind == "export":
        return run_export(request)
    print(f"unknown request kind {kind!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.exit(code)
