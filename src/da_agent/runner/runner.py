"""Runner: the only component that starts sandbox containers. See docs/adr/0005 and 0009.

Callers send typed requests. The Runner alone decides image, mounts, flags, and limits, so a request
cannot widen what a sandbox may do. Code travels on stdin and never becomes part of the command.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import DatasetSpec
from da_agent.storage.csv_export import EXPORT_TIMEOUT_SECONDS, MAX_EXPORT_BYTES, ExportReport

# Containers carry this label, so leftovers can be found and removed.
SANDBOX_LABEL = "da-agent.sandbox"
# Extra time for container start and teardown before the Runner kills the container itself.
STARTUP_GRACE_SECONDS = 15
MAX_CODE_CHARS = 50_000
MAX_SQL_CHARS = 20_000
_CHUNK = 65_536

# 128 + signal number: SIGKILL (memory limit or docker kill) and SIGALRM (the in-sandbox timer).
_EXIT_KILLED = 137
_EXIT_ALARM = 142

RunStatus = Literal["ok", "error", "timeout", "killed"]


class PythonRun(BaseModel):
    """Run one Python snippet against one dataset, mounted read-only at /data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["python"] = "python"
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    code: str = Field(max_length=MAX_CODE_CHARS)
    timeout_seconds: int = Field(default=20, ge=1, le=60)


class SqlRun(BaseModel):
    """Run one read-only query on one clean dataset, mounted read-only at /data. See ADR 0011."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["sql"] = "sql"
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    sql: str = Field(max_length=MAX_SQL_CHARS)
    max_rows: int = Field(default=50, ge=1, le=500)
    timeout_seconds: int = Field(default=15, ge=1, le=60)


class SqlResult(BaseModel):
    status: RunStatus
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False
    # A message for the model. Sandbox stderr is never passed on.
    error: str | None = None
    duration_seconds: float = 0.0


class IngestRun(BaseModel):
    """Parse one upload, mounted read-only at /input, into a fresh output directory at /out."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ingest"] = "ingest"
    upload_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    timeout_seconds: int = Field(default=600, ge=1, le=1800)


class BuildRun(BaseModel):
    """Build clean tables from an ingest output, mounted read-only at /input, into /out."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["build"] = "build"
    ingest_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    spec: DatasetSpec
    timeout_seconds: int = Field(default=600, ge=1, le=1800)


class ExportRun(BaseModel):
    """Write one query's whole result as CSV: the dataset is mounted read-only at /data and a fresh
    output directory at /out. See docs/design/2026-11-14-result-export.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["export"] = "export"
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    sql: str = Field(max_length=MAX_SQL_CHARS)
    timeout_seconds: int = Field(default=EXPORT_TIMEOUT_SECONDS, ge=1, le=EXPORT_TIMEOUT_SECONDS)


class JobResult[ReportT: BaseModel](BaseModel):
    status: RunStatus
    exit_code: int | None
    stderr: str
    report: ReportT | None
    # Set only when the run succeeded and the output passed every check.
    output_dir: Path | None
    duration_seconds: float


IngestResult = JobResult[IngestReport]
BuildResult = JobResult[BuildReport]
ExportResult = JobResult[ExportReport]
# The CSV cap plus room for the report next to it.
_MAX_EXPORT_OUTPUT_BYTES = MAX_EXPORT_BYTES + 1_048_576


class RunResult(BaseModel):
    status: RunStatus
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    duration_seconds: float


class SandboxRunner(Protocol):
    """What callers need from a Runner: the in-process one, or RunnerClient (ADR 0024)."""

    def query(self, request: SqlRun) -> SqlResult: ...
    def ingest(self, request: IngestRun) -> IngestResult: ...
    def build(self, request: BuildRun) -> BuildResult: ...
    def export(self, request: ExportRun) -> ExportResult: ...


@dataclass(frozen=True)
class RunnerConfig:
    """Operator settings. None of these can be set by a request."""

    image: str
    datasets_root: Path
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64
    tmp_mb: int = 64
    user: str = "65534:65534"
    max_output_bytes: int = 100_000
    docker: str = "docker"
    uploads_root: Path | None = None
    staging_root: Path | None = None
    ingest_memory_mb: int = 2048
    max_ingest_output_bytes: int = 5_000_000_000
    max_report_bytes: int = 20_000_000
    # Set when the Runner itself runs in a container (ADR 0024): sandboxes mount subpaths of this
    # named volume, which the Runner sees mounted at data_root, instead of host bind mounts.
    data_volume: str | None = None
    data_root: Path | None = None

    def __post_init__(self) -> None:
        if self.data_volume is None:
            return
        if self.data_root is None:
            raise ValueError("a data volume needs data_root, where the Runner sees it mounted")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.data_volume):
            raise ValueError(f"invalid volume name {self.data_volume!r}")


@dataclass(frozen=True)
class Captured:
    exit_code: int
    stdout: bytes
    stderr: bytes
    truncated: bool


def _drain(stream: IO[bytes], limit: int, kept: bytearray, overflow: list[bool]) -> None:
    """Keep the first `limit` bytes and discard the rest, so the process never blocks on a pipe."""
    while chunk := stream.read(_CHUNK):
        room = limit - len(kept)
        if room > 0:
            kept.extend(chunk[:room])
        if len(chunk) > max(room, 0):
            overflow[0] = True


def _not_started(error: OSError) -> str:
    """Docker missing or not running. Says what failed without paths or host details."""
    return f"the sandbox could not start ({type(error).__name__}); try again later"


def bounded_execute(command: list[str], *, input: bytes, timeout: float, limit: int) -> Captured:
    """Run a command with bounded memory for its output. Raises subprocess.TimeoutExpired."""
    # The Runner builds `command` from its own config. Request data only travels on stdin.
    process = subprocess.Popen(  # noqa: S603
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("subprocess pipes were not created")
    out, err, overflow = bytearray(), bytearray(), [False]
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, limit, out, overflow), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, limit, err, overflow), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(input)
        process.stdin.close()
    except BrokenPipeError:
        pass
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    for reader in readers:
        reader.join(timeout=5)
    return Captured(exit_code, bytes(out), bytes(err), truncated=overflow[0])


class Runner:
    def __init__(
        self,
        config: RunnerConfig,
        *,
        execute: Callable[..., Captured] = bounded_execute,
        kill: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._execute = execute
        self._kill = kill or self._docker_kill

    def build_command(
        self,
        request: PythonRun | SqlRun | IngestRun | BuildRun | ExportRun,
        name: str,
        output_dir: Path | None = None,
    ) -> list[str]:
        c = self.config
        if isinstance(request, (IngestRun, BuildRun)):
            if output_dir is None:
                raise ValueError("a job run needs an output directory")
            source = self._job_input(request)
            memory = c.ingest_memory_mb
            mounts = [self._mount(source, "/input", True), self._mount(output_dir, "/out", False)]
        elif isinstance(request, ExportRun):
            if output_dir is None:
                raise ValueError("an export needs an output directory")
            dataset = self._child(c.datasets_root, request.dataset_id, "dataset")
            memory = c.memory_mb
            mounts = [self._mount(dataset, "/data", True), self._mount(output_dir, "/out", False)]
        else:
            dataset = self._child(c.datasets_root, request.dataset_id, "dataset")
            memory = c.memory_mb
            mounts = [self._mount(dataset, "/data", True)]
        return [
            c.docker,
            "run",
            "--rm",
            "-i",
            f"--name={name}",
            f"--label={SANDBOX_LABEL}=1",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={c.user}",
            f"--memory={memory}m",
            f"--memory-swap={memory}m",
            f"--cpus={c.cpus}",
            f"--pids-limit={c.pids_limit}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={c.tmp_mb}m",
            *mounts,
            c.image,
        ]

    def query(self, request: SqlRun) -> SqlResult:
        """Run one query in a fresh sandbox and return a bounded result or an error message."""
        run = self.run(request)
        if run.status == "timeout":
            message = f"query stopped: over the {request.timeout_seconds} s time limit"
            return SqlResult(status="timeout", error=message, duration_seconds=run.duration_seconds)
        if run.status == "killed":
            message = "query stopped: over the memory limit; aggregate or filter more"
            return SqlResult(status="killed", error=message, duration_seconds=run.duration_seconds)
        if run.status == "error" and run.exit_code is None:
            return SqlResult(
                status="error", error=run.stderr, duration_seconds=run.duration_seconds
            )
        try:
            payload = json.loads(run.stdout) if run.status == "ok" else None
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            return SqlResult(
                status="error",
                error="the query sandbox returned no result",
                duration_seconds=run.duration_seconds,
            )
        if "error" in payload:
            return SqlResult(
                status="error",
                error=str(payload["error"])[:500],
                duration_seconds=run.duration_seconds,
            )
        try:
            return SqlResult(
                status="ok",
                columns=payload.get("columns", []),
                rows=payload.get("rows", []),
                truncated=bool(payload.get("truncated")) or run.truncated,
                duration_seconds=run.duration_seconds,
            )
        except ValidationError:
            return SqlResult(
                status="error",
                error="the query sandbox returned no result",
                duration_seconds=run.duration_seconds,
            )

    def run(self, request: PythonRun | SqlRun) -> RunResult:
        name = f"da-sbx-{uuid.uuid4().hex[:12]}"
        command = self.build_command(request, name)
        payload = json.dumps(request.model_dump()).encode()
        limit = self.config.max_output_bytes
        started = _now()
        try:
            captured = self._execute(
                command,
                input=payload,
                timeout=request.timeout_seconds + STARTUP_GRACE_SECONDS,
                limit=limit,
            )
        except subprocess.TimeoutExpired:
            self._kill(name)
            return RunResult(
                status="timeout",
                exit_code=None,
                stdout="",
                stderr="",
                truncated=False,
                duration_seconds=_now() - started,
            )
        except OSError as error:
            return RunResult(
                status="error",
                exit_code=None,
                stdout="",
                stderr=_not_started(error),
                truncated=False,
                duration_seconds=_now() - started,
            )
        return RunResult(
            status=_status(captured.exit_code),
            exit_code=captured.exit_code,
            stdout=captured.stdout[:limit].decode("utf-8", errors="replace"),
            stderr=captured.stderr[:limit].decode("utf-8", errors="replace"),
            truncated=captured.truncated or max(len(captured.stdout), len(captured.stderr)) > limit,
            duration_seconds=_now() - started,
        )

    def _mount(self, source: Path, target: str, readonly: bool) -> str:
        flag = ",readonly" if readonly else ""
        c = self.config
        if c.data_volume is None or c.data_root is None:
            return f"--mount=type=bind,source={source},target={target}{flag}"
        # Raises if source is outside the volume; sources are already resolved and checked.
        subpath = Path(source).resolve().relative_to(c.data_root.resolve())
        return (
            f"--mount=type=volume,source={c.data_volume},target={target}{flag},"
            f"volume-subpath={subpath.as_posix()}"
        )

    @staticmethod
    def _child(root: Path | None, child_id: str, what: str) -> Path:
        if root is None:
            raise ValueError(f"no {what} root is configured")
        resolved_root = root.resolve()
        path = (resolved_root / child_id).resolve()
        # resolve() follows symlinks, so a link that points outside the root fails this check.
        if path.parent != resolved_root or not path.is_dir():
            raise ValueError(f"unknown {what} {child_id!r}")
        return path

    def _job_input(self, request: IngestRun | BuildRun | ExportRun) -> Path:
        if isinstance(request, ExportRun):
            return self._child(self.config.datasets_root, request.dataset_id, "dataset")
        if isinstance(request, IngestRun):
            return self._child(self.config.uploads_root, request.upload_id, "upload")
        return self._child(self.config.staging_root, request.ingest_id, "ingest output")

    def ingest(self, request: IngestRun) -> IngestResult:
        """Run the raw ingestion stage in a sandbox. Output files are checked before use."""
        return self._job(request, "report.json", IngestReport)

    def build(self, request: BuildRun) -> BuildResult:
        """Build clean tables from an ingest output in a sandbox."""
        return self._job(request, "build_report.json", BuildReport)

    def export(self, request: ExportRun) -> ExportResult:
        """Write one query's whole result as CSV in a sandbox. A cap or query error comes back in
        the report's error, never as sandbox stderr."""
        return self._job(
            request, "export_report.json", ExportReport, max_output=_MAX_EXPORT_OUTPUT_BYTES
        )

    def _job[ReportT: BaseModel](
        self,
        request: IngestRun | BuildRun | ExportRun,
        report_name: str,
        model: type[ReportT],
        *,
        max_output: int | None = None,
    ) -> JobResult[ReportT]:
        self._job_input(request)
        if self.config.staging_root is None:
            raise ValueError("no staging root is configured")
        staging = self.config.staging_root.resolve()
        name = f"da-sbx-{uuid.uuid4().hex[:12]}"
        output_dir = staging / name
        output_dir.mkdir()
        # The sandbox user is not the Runner's user, so it needs write access to this one directory.
        output_dir.chmod(0o777)
        command = self.build_command(request, name, output_dir)
        payload = request.model_dump_json().encode()
        started = _now()

        def failure(status: RunStatus, exit_code: int | None, stderr: str) -> JobResult[ReportT]:
            shutil.rmtree(output_dir, ignore_errors=True)
            return JobResult[model](
                status=status,
                exit_code=exit_code,
                stderr=stderr,
                report=None,
                output_dir=None,
                duration_seconds=_now() - started,
            )

        try:
            captured = self._execute(
                command,
                input=payload,
                timeout=request.timeout_seconds + STARTUP_GRACE_SECONDS,
                limit=self.config.max_output_bytes,
            )
        except subprocess.TimeoutExpired:
            self._kill(name)
            return failure("timeout", None, "")
        except OSError as error:
            return failure("error", None, _not_started(error))
        stderr = captured.stderr[: self.config.max_output_bytes].decode("utf-8", errors="replace")
        status = _status(captured.exit_code)
        if status != "ok":
            return failure(status, captured.exit_code, stderr)
        try:
            limit = max_output if max_output is not None else self.config.max_ingest_output_bytes
            report = model.model_validate_json(self._read_report(output_dir, report_name, limit))
        except (OSError, ValueError, ValidationError) as error:
            message = f"{stderr}\nrejected sandbox output: {type(error).__name__}: {error}"[-2000:]
            return failure("error", captured.exit_code, message)
        return JobResult[model](
            status="ok",
            exit_code=captured.exit_code,
            stderr=stderr,
            report=report,
            output_dir=output_dir,
            duration_seconds=_now() - started,
        )

    def _read_report(self, output_dir: Path, report_name: str, max_output: int) -> bytes:
        total = 0
        for entry in output_dir.rglob("*"):
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"unexpected file type at {entry.name}")
            total += entry.lstat().st_size
        if total > max_output:
            raise ValueError(f"output is {total} bytes, over the limit")
        # O_NOFOLLOW: a link that appeared after the scan makes the open fail, not follow it.
        descriptor = os.open(output_dir / report_name, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            data = file.read(self.config.max_report_bytes + 1)
        if len(data) > self.config.max_report_bytes:
            raise ValueError("report is over the size limit")
        return data

    def _docker_kill(self, name: str) -> None:
        # `name` is generated by this class, never taken from a request.
        subprocess.run(  # noqa: S603
            [self.config.docker, "kill", name], capture_output=True, check=False, timeout=30
        )


def _status(exit_code: int) -> RunStatus:
    if exit_code == 0:
        return "ok"
    if exit_code == _EXIT_ALARM:
        return "timeout"
    if exit_code == _EXIT_KILLED:
        return "killed"
    return "error"


def _now() -> float:
    return time.monotonic()
