"""Runner: the only component that starts sandbox containers. See docs/adr/0005 and 0009.

Callers send typed requests. The Runner alone decides image, mounts, flags, and limits, so a request
cannot widen what a sandbox may do. Code travels on stdin and never becomes part of the command.
"""

import json
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict, Field

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

    def build_command(self, request: PythonRun, name: str) -> list[str]:
        dataset = self._dataset_path(request.dataset_id)
        c = self.config
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
            f"--memory={c.memory_mb}m",
            f"--memory-swap={c.memory_mb}m",
            f"--cpus={c.cpus}",
            f"--pids-limit={c.pids_limit}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={c.tmp_mb}m",
            f"--mount=type=bind,source={dataset},target=/data,readonly",
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

    def _dataset_path(self, dataset_id: str) -> Path:
        root = self.config.datasets_root.resolve()
        path = (root / dataset_id).resolve()
        # resolve() follows symlinks, so a link that points outside the root fails this check.
        if path.parent != root or not path.is_dir():
            raise ValueError(f"unknown dataset {dataset_id!r}")
        return path

    def _docker_kill(self, name: str) -> None:
        # `name` is generated by run(), never taken from a request.
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
