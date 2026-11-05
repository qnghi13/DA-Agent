"""Runner command building and result handling, without Docker. Containers are in tests/security."""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from da_agent.runner.runner import Captured, PythonRun, Runner, RunnerConfig

ROOT_FLAGS = {
    "--network=none",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
}


@pytest.fixture
def config(tmp_path: Path) -> RunnerConfig:
    (tmp_path / "shop_1").mkdir()
    return RunnerConfig(image="da-agent-sandbox:test", datasets_root=tmp_path)


def test_command_applies_every_isolation_flag(config: RunnerConfig) -> None:
    command = Runner(config).build_command(PythonRun(dataset_id="shop_1", code="print(1)"), "n1")

    assert set(command) >= ROOT_FLAGS
    assert f"--memory={config.memory_mb}m" in command
    assert f"--memory-swap={config.memory_mb}m" in command
    assert f"--pids-limit={config.pids_limit}" in command
    assert f"--user={config.user}" in command
    assert command[-1] == config.image


def test_no_environment_or_extra_mounts_are_passed(config: RunnerConfig) -> None:
    command = Runner(config).build_command(PythonRun(dataset_id="shop_1", code="x"), "n1")

    forbidden = ("-e", "--env", "--env-file", "-v", "--volume", "--privileged", "--network=host")
    assert not [arg for arg in command if arg.split("=")[0] in forbidden]


def test_only_the_requested_dataset_is_mounted_read_only(config: RunnerConfig) -> None:
    command = Runner(config).build_command(PythonRun(dataset_id="shop_1", code="x"), "n1")

    mounts = [arg for arg in command if arg.startswith("--mount=")]
    dataset = (config.datasets_root / "shop_1").resolve()
    assert mounts == [f"--mount=type=bind,source={dataset},target=/data,readonly"]


def test_code_goes_to_stdin_and_never_into_the_command(config: RunnerConfig) -> None:
    code = "--privileged -v /:/host --network=host"
    calls: list[dict] = []

    def fake_execute(command: list[str], **kwargs) -> Captured:
        calls.append({"command": command, **kwargs})
        return Captured(0, b"ok", b"", truncated=False)

    Runner(config, execute=fake_execute).run(PythonRun(dataset_id="shop_1", code=code))

    assert all(code not in arg for arg in calls[0]["command"])
    assert code.encode() in calls[0]["input"]


@pytest.mark.parametrize("dataset_id", ["../shop_1", "shop_1/..", "/etc", "a b", "", "x" * 65])
def test_dataset_ids_cannot_name_paths(dataset_id: str) -> None:
    with pytest.raises(ValidationError):
        PythonRun(dataset_id=dataset_id, code="x")


def test_unknown_datasets_are_refused(config: RunnerConfig) -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        Runner(config).build_command(PythonRun(dataset_id="shop_2", code="x"), "n1")


def test_a_symlinked_dataset_cannot_leave_the_root(config: RunnerConfig, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (config.datasets_root / "escape").symlink_to(outside)

    with pytest.raises(ValueError, match="unknown dataset"):
        Runner(config).build_command(PythonRun(dataset_id="escape", code="x"), "n1")


@pytest.mark.parametrize("timeout", [0, 61])
def test_timeouts_are_bounded(timeout: int) -> None:
    with pytest.raises(ValidationError):
        PythonRun(dataset_id="shop_1", code="x", timeout_seconds=timeout)


def test_requests_cannot_carry_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PythonRun.model_validate({"dataset_id": "shop_1", "code": "x", "memory_mb": 99999})


def test_output_is_cut_to_the_limit(config: RunnerConfig) -> None:
    def fake_execute(command: list[str], **kwargs) -> Captured:
        assert kwargs["limit"] == 10
        return Captured(0, b"a" * 10, b"b" * 10, truncated=True)

    small = RunnerConfig(
        image=config.image, datasets_root=config.datasets_root, max_output_bytes=10
    )
    result = Runner(small, execute=fake_execute).run(PythonRun(dataset_id="shop_1", code="x"))

    assert (result.stdout, result.stderr) == ("a" * 10, "b" * 10)
    assert result.truncated


def test_a_timeout_kills_the_container_and_reports_it(config: RunnerConfig) -> None:
    started: list[list[str]] = []
    killed: list[str] = []

    def fake_execute(command: list[str], **kwargs) -> Captured:
        started.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    runner = Runner(config, execute=fake_execute, kill=killed.append)
    result = runner.run(PythonRun(dataset_id="shop_1", code="x"))

    assert result.status == "timeout"
    name = next(arg for arg in started[0] if arg.startswith("--name=")).removeprefix("--name=")
    assert killed == [name]


@pytest.mark.parametrize(
    ("exit_code", "status"), [(0, "ok"), (1, "error"), (137, "killed"), (142, "timeout")]
)
def test_exit_codes_map_to_a_status(config: RunnerConfig, exit_code: int, status: str) -> None:
    def fake_execute(command: list[str], **kwargs) -> Captured:
        return Captured(exit_code, b"", b"", truncated=False)

    result = Runner(config, execute=fake_execute).run(PythonRun(dataset_id="shop_1", code="x"))

    assert (result.status, result.exit_code) == (status, exit_code)


def test_bounded_execute_keeps_only_the_limit_and_drains_the_rest() -> None:
    from da_agent.runner.runner import bounded_execute

    script = "import sys; sys.stdout.write('x' * 1_000_000); sys.stderr.write('e' * 10)"
    captured = bounded_execute(["python3", "-c", script], input=b"", timeout=10, limit=100)

    assert (captured.exit_code, len(captured.stdout), captured.stderr) == (0, 100, b"e" * 10)
    assert captured.truncated
