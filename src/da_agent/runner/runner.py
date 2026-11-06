"""Runner: the only component that starts sandbox containers. See docs/adr/0005 and 0009.

Callers send typed requests. The Runner alone decides image, mounts, flags, and limits, so a request
cannot widen what a sandbox may do. Code travels on stdin and never becomes part of the command.
"""

import json
import os
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from da_agent.ingestion.build import BuildReport
from da_agent.ingestion.report import IngestReport
from da_agent.ingestion.spec import DatasetSpec

# Containers carry this label, so leftovers can be found and removed.
SANDBOX_LABEL = "da-agent.sandbox"
# Extra time for container start and teardown before the Runner kills the container itself.
STARTUP_GRACE_SECONDS = 15
MAX_CODE_CHARS = 50_000
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


class RunResult(BaseModel):
    status: RunStatus
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    duration_seconds: float


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
        self, request: PythonRun | IngestRun | BuildRun, name: str, output_dir: Path | None = None
    ) -> list[str]:
        c = self.config
        if isinstance(request, (IngestRun, BuildRun)):
            if output_dir is None:
                raise ValueError("a job run needs an output directory")
            source = self._job_input(request)
            memory = c.ingest_memory_mb
            mounts = [
                f"--mount=type=bind,source={source},target=/input,readonly",
                f"--mount=type=bind,source={output_dir},target=/out",
            ]
        else:
            dataset = self._child(c.datasets_root, request.dataset_id, "dataset")
            memory = c.memory_mb
            mounts = [f"--mount=type=bind,source={dataset},target=/data,readonly"]
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

    def run(self, request: PythonRun) -> RunResult:
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
        return RunResult(
            status=_status(captured.exit_code),
            exit_code=captured.exit_code,
            stdout=captured.stdout[:limit].decode("utf-8", errors="replace"),
            stderr=captured.stderr[:limit].decode("utf-8", errors="replace"),
            truncated=captured.truncated or max(len(captured.stdout), len(captured.stderr)) > limit,
            duration_seconds=_now() - started,
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

    def _job_input(self, request: IngestRun | BuildRun) -> Path:
        if isinstance(request, IngestRun):
            return self._child(self.config.uploads_root, request.upload_id, "upload")
        return self._child(self.config.staging_root, request.ingest_id, "ingest output")

    def ingest(self, request: IngestRun) -> IngestResult:
        """Run the raw ingestion stage in a sandbox. Output files are checked before use."""
        return self._job(request, "report.json", IngestReport)

    def build(self, request: BuildRun) -> BuildResult:
        """Build clean tables from an ingest output in a sandbox."""
        return self._job(request, "build_report.json", BuildReport)

    def _job[ReportT: BaseModel](
        self, request: IngestRun | BuildRun, report_name: str, model: type[ReportT]
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
        stderr = captured.stderr[: self.config.max_output_bytes].decode("utf-8", errors="replace")
        status = _status(captured.exit_code)
        if status != "ok":
            return failure(status, captured.exit_code, stderr)
        try:
            report = model.model_validate_json(self._read_report(output_dir, report_name))
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

    def _read_report(self, output_dir: Path, report_name: str) -> bytes:
        total = 0
        for entry in output_dir.rglob("*"):
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"unexpected file type at {entry.name}")
            total += entry.lstat().st_size
        if total > self.config.max_ingest_output_bytes:
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
