"""Escape attempts against a real sandbox container. Every attempt must fail inside the sandbox.

Needs Docker. Skipped when Docker is not available.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from da_agent.runner.runner import PythonRun, Runner, RunnerConfig

pytestmark = [pytest.mark.integration, pytest.mark.security]

REPO = Path(__file__).resolve().parents[2]
IMAGE = "da-agent-sandbox:test"
CANARY = "SANDBOX-CANARY-4417"


def build_image() -> None:
    subprocess.run(
        ["docker", "build", "-q", "-f", str(REPO / "sandbox/Dockerfile"), "-t", IMAGE, str(REPO)],
        check=True,
        capture_output=True,
    )


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> Runner:
    if not _docker_ready():
        pytest.skip("Docker is not available")
    build_image()
    root = tmp_path_factory.mktemp("datasets")
    root.chmod(0o755)
    shop = root / "shop_1"
    shop.mkdir(mode=0o755)
    (shop / "sales.csv").write_text("id,total\n1,100\n", encoding="utf-8")
    (shop / "sales.csv").chmod(0o644)
    # A sibling dataset and an answers file next to it. Neither may be visible in the sandbox.
    (root / "shop_2").mkdir()
    (root / "shop_2" / "secret.csv").write_text(CANARY, encoding="utf-8")
    (root / "answers.jsonl").write_text(CANARY, encoding="utf-8")
    return Runner(RunnerConfig(image=IMAGE, datasets_root=root, memory_mb=256))


def run(runner: Runner, code: str, timeout: int = 20):
    return runner.run(PythonRun(dataset_id="shop_1", code=code, timeout_seconds=timeout))


def test_code_runs_and_reads_its_own_dataset(runner: Runner) -> None:
    result = run(runner, "print(open('/data/sales.csv').read().splitlines()[1])")

    assert result.status == "ok", result.stderr
    assert result.stdout.strip() == "1,100"


def test_errors_come_back_as_an_error_status(runner: Runner) -> None:
    result = run(runner, "raise ValueError('boom')")

    assert result.status == "error"
    assert "ValueError: boom" in result.stderr


def test_no_network(runner: Runner) -> None:
    code = """
import socket
for target in [("1.1.1.1", 53), ("example.com", 80)]:
    try:
        socket.create_connection(target, timeout=3)
        print("CONNECTED", target)
    except OSError as error:
        print("blocked", type(error).__name__)
"""
    result = run(runner, code)

    assert result.status == "ok", result.stderr
    assert "CONNECTED" not in result.stdout


def test_filesystem_is_read_only_except_a_small_tmp(runner: Runner) -> None:
    code = """
for path in ["/data/new.csv", "/data/sales.csv", "/etc/passwd", "/root.txt", "/usr/x"]:
    try:
        open(path, "a").write("x")
        print("WROTE", path)
    except OSError:
        pass
open("/tmp/scratch.txt", "w").write("ok")
print("tmp ok")
"""
    result = run(runner, code)

    assert "WROTE" not in result.stdout
    assert "tmp ok" in result.stdout


def test_no_secrets_in_the_environment(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", CANARY)

    result = run(runner, "import os; print(dict(os.environ))")

    assert result.status == "ok", result.stderr
    assert CANARY not in result.stdout
    assert "API_KEY" not in result.stdout


def test_other_datasets_answers_and_the_docker_socket_are_not_reachable(runner: Runner) -> None:
    code = """
import os
found = []
for top in ["/data", "/tmp", "/home", "/root", "/mnt", "/srv", "/opt", "/var", "/run"]:
    for root, dirs, files in os.walk(top):
        found += [os.path.join(root, f) for f in files]
print("\\n".join(found))
print("socket", os.path.exists("/var/run/docker.sock") or os.path.exists("/run/docker.sock"))
"""
    result = run(runner, code)

    assert result.status == "ok", result.stderr
    assert "secret.csv" not in result.stdout
    assert "answers.jsonl" not in result.stdout
    assert "socket False" in result.stdout


def test_runs_as_an_unprivileged_user_with_no_capabilities(runner: Runner) -> None:
    code = """
import os
print("uid", os.getuid())
for line in open("/proc/self/status"):
    if line.startswith(("CapEff", "CapPrm", "NoNewPrivs")):
        print(line.strip())
"""
    result = run(runner, code)

    assert "uid 0" not in result.stdout
    assert "CapEff:\t0000000000000000" in result.stdout
    assert "CapPrm:\t0000000000000000" in result.stdout
    assert "NoNewPrivs:\t1" in result.stdout


def test_a_fork_bomb_hits_the_process_limit(runner: Runner) -> None:
    code = """
import os
children = 0
try:
    while True:
        if os.fork() == 0:
            import time; time.sleep(30); os._exit(0)
        children += 1
except OSError:
    print("limited at", children)
"""
    started = time.monotonic()
    result = run(runner, code, timeout=15)

    assert "limited at" in result.stdout
    assert int(result.stdout.split()[-1]) < 200
    assert time.monotonic() - started < 40


def test_memory_exhaustion_kills_only_the_sandbox(runner: Runner) -> None:
    result = run(runner, "blocks = []\nwhile True:\n    blocks.append(bytearray(50_000_000))")

    assert result.status == "killed"


def test_an_endless_loop_times_out_and_leaves_no_container(runner: Runner) -> None:
    started = time.monotonic()

    result = run(runner, "while True:\n    pass", timeout=2)

    assert result.status == "timeout"
    assert time.monotonic() - started < 20
    assert _no_sandbox_left(within_seconds=10)


def _no_sandbox_left(within_seconds: float) -> bool:
    """Docker removes --rm containers shortly after they exit, so poll for a while."""
    deadline = time.monotonic() + within_seconds
    while True:
        left = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "label=da-agent.sandbox"],
            capture_output=True,
            text=True,
            check=True,
        )
        if not left.stdout.strip():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)


def test_huge_output_is_truncated(runner: Runner) -> None:
    result = run(runner, "print('x' * 5_000_000)")

    assert result.truncated
    assert len(result.stdout) <= runner.config.max_output_bytes
