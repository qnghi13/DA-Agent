"""Runner command building and result handling, without Docker. Containers are in tests/security."""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from da_agent.runner.runner import Captured, PythonRun, Runner, RunnerConfig, SqlRun

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


# Ingest jobs: the upload is mounted read-only, and a fresh output directory is the only writable
# mount. The Runner treats everything in that directory as untrusted.

from da_agent.runner.runner import IngestRun  # noqa: E402


@pytest.fixture
def ingest_config(tmp_path: Path) -> RunnerConfig:
    (tmp_path / "uploads" / "up_1").mkdir(parents=True)
    (tmp_path / "staging").mkdir()
    (tmp_path / "datasets").mkdir()
    return RunnerConfig(
        image="da-agent-sandbox:test",
        datasets_root=tmp_path / "datasets",
        uploads_root=tmp_path / "uploads",
        staging_root=tmp_path / "staging",
    )


def output_mount(command: list[str]) -> Path:
    mount = next(arg for arg in command if "target=/out" in arg)
    return Path(mount.split("source=", 1)[1].split(",", 1)[0])


def test_ingest_mounts_the_upload_read_only_and_a_fresh_output(ingest_config: RunnerConfig) -> None:
    out = ingest_config.staging_root / "job"
    out.mkdir()
    command = Runner(ingest_config).build_command(IngestRun(upload_id="up_1"), "n1", out)

    upload = (ingest_config.uploads_root / "up_1").resolve()
    mounts = [arg for arg in command if arg.startswith("--mount=")]
    assert mounts == [
        f"--mount=type=bind,source={upload},target=/input,readonly",
        f"--mount=type=bind,source={out},target=/out",
    ]
    assert f"--memory={ingest_config.ingest_memory_mb}m" in command
    assert set(command) >= ROOT_FLAGS


def test_unknown_uploads_are_refused(ingest_config: RunnerConfig) -> None:
    with pytest.raises(ValueError, match="unknown upload"):
        Runner(ingest_config).ingest(IngestRun(upload_id="up_2"))


def fake_ingest(write) -> object:
    def execute(command: list[str], **kwargs) -> Captured:
        write(output_mount(command))
        return Captured(0, b"", b"", truncated=False)

    return execute


def test_ingest_returns_the_report_and_the_output_directory(ingest_config: RunnerConfig) -> None:
    def write(out: Path) -> None:
        (out / "report.json").write_text('{"tables": [], "warnings": ["w"]}', encoding="utf-8")
        (out / "raw.duckdb").write_bytes(b"db")

    result = Runner(ingest_config, execute=fake_ingest(write)).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "ok"
    assert result.report is not None and result.report.warnings == ["w"]
    assert result.output_dir is not None and (result.output_dir / "raw.duckdb").exists()
    assert result.output_dir.parent == ingest_config.staging_root.resolve()


def test_a_symlink_in_the_output_is_refused(ingest_config: RunnerConfig, tmp_path: Path) -> None:
    secret = tmp_path / "host_secret.txt"
    secret.write_text('{"warnings": ["HOST-SECRET"]}', encoding="utf-8")

    def write(out: Path) -> None:
        (out / "report.json").symlink_to(secret)

    result = Runner(ingest_config, execute=fake_ingest(write)).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "error"
    assert result.report is None
    assert "HOST-SECRET" not in result.model_dump_json()


def test_an_oversized_output_is_refused_and_removed(ingest_config: RunnerConfig) -> None:
    small = RunnerConfig(**{**ingest_config.__dict__, "max_ingest_output_bytes": 100})

    def write(out: Path) -> None:
        (out / "report.json").write_text("{}", encoding="utf-8")
        (out / "raw.duckdb").write_bytes(b"x" * 1000)

    result = Runner(small, execute=fake_ingest(write)).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "error"
    assert result.output_dir is None
    assert list(small.staging_root.iterdir()) == []


def test_a_missing_or_invalid_report_is_an_error(ingest_config: RunnerConfig) -> None:
    for content in (None, "not json", '{"tables": "wrong"}'):

        def write(out: Path, content: str | None = content) -> None:
            if content is not None:
                (out / "report.json").write_text(content, encoding="utf-8")

        runner = Runner(ingest_config, execute=fake_ingest(write))
        result = runner.ingest(IngestRun(upload_id="up_1"))

        assert result.status == "error", content
        assert result.report is None


# Build jobs: the ingest output is mounted read-only, and a fresh output directory receives the
# clean dataset.

from da_agent.ingestion.spec import DatasetSpec  # noqa: E402
from da_agent.runner.runner import BuildRun  # noqa: E402


def test_build_mounts_the_ingest_output_read_only(ingest_config: RunnerConfig) -> None:
    (ingest_config.staging_root / "ing_1").mkdir()
    out = ingest_config.staging_root / "job"
    out.mkdir()
    request = BuildRun(ingest_id="ing_1", spec=DatasetSpec(tables=[]))

    command = Runner(ingest_config).build_command(request, "n1", out)

    source = (ingest_config.staging_root / "ing_1").resolve()
    mounts = [arg for arg in command if arg.startswith("--mount=")]
    assert mounts == [
        f"--mount=type=bind,source={source},target=/input,readonly",
        f"--mount=type=bind,source={out},target=/out",
    ]


def test_build_returns_the_build_report(ingest_config: RunnerConfig) -> None:
    (ingest_config.staging_root / "ing_1").mkdir()

    def write(out: Path) -> None:
        (out / "build_report.json").write_text(
            '{"tables": [{"name": "t", "source": "t", "rows": 3}]}', encoding="utf-8"
        )
        (out / "dataset.duckdb").write_bytes(b"db")

    runner = Runner(ingest_config, execute=fake_ingest(write))
    result = runner.build(BuildRun(ingest_id="ing_1", spec=DatasetSpec(tables=[])))

    assert result.status == "ok"
    assert result.report is not None and result.report.tables[0].rows == 3
    assert result.output_dir is not None and (result.output_dir / "dataset.duckdb").exists()


def test_build_refuses_unknown_ingest_outputs(ingest_config: RunnerConfig) -> None:
    with pytest.raises(ValueError, match="unknown ingest output"):
        Runner(ingest_config).build(BuildRun(ingest_id="ing_9", spec=DatasetSpec(tables=[])))


def sql_runner(config: RunnerConfig, captured: Captured, calls: list | None = None) -> Runner:
    def fake_execute(command: list[str], **kwargs) -> Captured:
        if calls is not None:
            calls.append({"command": command, **kwargs})
        return captured

    return Runner(config, execute=fake_execute)


def test_sql_mounts_only_the_dataset_and_sends_the_query_on_stdin(config: RunnerConfig) -> None:
    calls: list = []
    output = b'{"columns": ["n"], "rows": [[3]], "truncated": false}'
    runner = sql_runner(config, Captured(0, output, b"", truncated=False), calls)

    result = runner.query(SqlRun(dataset_id="shop_1", sql="SELECT 3 AS n"))

    command = calls[0]["command"]
    mounts = [arg for arg in command if arg.startswith("--mount=")]
    dataset = (config.datasets_root / "shop_1").resolve()
    assert mounts == [f"--mount=type=bind,source={dataset},target=/data,readonly"]
    assert set(command) >= ROOT_FLAGS
    assert all("SELECT 3" not in arg for arg in command)
    assert b"SELECT 3 AS n" in calls[0]["input"]
    assert (result.status, result.columns, result.rows, result.error) == ("ok", ["n"], [[3]], None)


def test_a_query_error_comes_back_as_a_message(config: RunnerConfig) -> None:
    output = b'{"error": "Binder Error: column missing not found"}'
    runner = sql_runner(config, Captured(0, output, b"", truncated=False))

    result = runner.query(SqlRun(dataset_id="shop_1", sql="SELECT missing"))

    assert result.status == "error"
    assert result.error == "Binder Error: column missing not found"


@pytest.mark.parametrize(
    ("captured", "status", "words"),
    [
        (Captured(142, b"", b"", truncated=False), "timeout", "time limit"),
        (Captured(137, b"", b"", truncated=False), "killed", "memory"),
        (Captured(0, b"not json", b"", truncated=False), "error", "no result"),
        (Captured(1, b"", b"Traceback", truncated=False), "error", "no result"),
    ],
)
def test_sandbox_failures_become_messages_the_model_can_act_on(
    config: RunnerConfig, captured: Captured, status: str, words: str
) -> None:
    result = sql_runner(config, captured).query(SqlRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result.status == status
    assert result.error is not None and words in result.error
    assert "Traceback" not in result.error


def broken_docker(command: list[str], **kwargs) -> Captured:
    raise FileNotFoundError(2, "No such file or directory", "docker")


def test_a_sandbox_that_cannot_start_is_a_query_error_not_a_crash(config: RunnerConfig) -> None:
    runner = Runner(config, execute=broken_docker)

    result = runner.query(SqlRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result.status == "error"
    assert result.error is not None and "could not start" in result.error


def test_a_job_whose_sandbox_cannot_start_fails_cleanly(ingest_config: RunnerConfig) -> None:
    result = Runner(ingest_config, execute=broken_docker).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "error"
    assert "could not start" in result.stderr
    assert result.output_dir is None
    assert list(ingest_config.staging_root.iterdir()) == []


@pytest.fixture
def volume_config(tmp_path: Path) -> RunnerConfig:
    for part in ("uploads/up_1", "staging", "datasets/shop_1"):
        (tmp_path / part).mkdir(parents=True)
    return RunnerConfig(
        image="da-agent-sandbox:test",
        datasets_root=tmp_path / "datasets",
        uploads_root=tmp_path / "uploads",
        staging_root=tmp_path / "staging",
        data_volume="da-agent_data",
        data_root=tmp_path,
    )


def test_with_a_data_volume_datasets_mount_a_subpath_not_a_host_path(
    volume_config: RunnerConfig,
) -> None:
    command = Runner(volume_config).build_command(SqlRun(dataset_id="shop_1", sql="SELECT 1"), "n1")

    mounts = [arg for arg in command if arg.startswith("--mount=")]
    assert mounts == [
        "--mount=type=volume,source=da-agent_data,target=/data,readonly,"
        "volume-subpath=datasets/shop_1"
    ]


def test_with_a_data_volume_jobs_mount_subpaths_of_it(volume_config: RunnerConfig) -> None:
    out = volume_config.staging_root / "job"
    out.mkdir()
    command = Runner(volume_config).build_command(IngestRun(upload_id="up_1"), "n1", out)

    mounts = [arg for arg in command if arg.startswith("--mount=")]
    assert mounts == [
        "--mount=type=volume,source=da-agent_data,target=/input,readonly,"
        "volume-subpath=uploads/up_1",
        "--mount=type=volume,source=da-agent_data,target=/out,volume-subpath=staging/job",
    ]


def test_a_data_volume_needs_the_root_it_is_mounted_at(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="data_root"):
        RunnerConfig(image="i", datasets_root=tmp_path, data_volume="v")


def test_the_volume_name_cannot_carry_mount_options(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="volume name"):
        RunnerConfig(
            image="i", datasets_root=tmp_path, data_volume="v,target=/x", data_root=tmp_path
        )


# Export jobs: the dataset is mounted read-only at /data, and a fresh output directory receives the
# CSV (docs/design/2026-11-14-result-export.md).

from da_agent.runner.runner import ExportRun  # noqa: E402
from da_agent.storage.csv_export import MAX_EXPORT_BYTES  # noqa: E402


@pytest.fixture
def export_config(ingest_config: RunnerConfig) -> RunnerConfig:
    (ingest_config.datasets_root / "shop_1").mkdir()
    return ingest_config


def test_an_export_mounts_the_dataset_read_only_and_a_fresh_out(
    export_config: RunnerConfig,
) -> None:
    out = export_config.staging_root / "job"
    out.mkdir()
    request = ExportRun(dataset_id="shop_1", sql="SELECT 1")
    command = Runner(export_config).build_command(request, "n1", out)

    dataset = (export_config.datasets_root / "shop_1").resolve()
    mounts = [arg for arg in command if arg.startswith("--mount=")]
    assert mounts == [
        f"--mount=type=bind,source={dataset},target=/data,readonly",
        f"--mount=type=bind,source={out},target=/out",
    ]
    assert f"--memory={export_config.memory_mb}m" in command
    assert set(command) >= ROOT_FLAGS


@pytest.mark.parametrize("dataset_id", ["../x", "a/b", ""])
def test_an_export_cannot_name_a_path(dataset_id: str) -> None:
    with pytest.raises(ValidationError):
        ExportRun(dataset_id=dataset_id, sql="SELECT 1")


def test_an_export_of_an_unknown_dataset_is_refused(export_config: RunnerConfig) -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        Runner(export_config).export(ExportRun(dataset_id="shop_2", sql="SELECT 1"))


def test_an_export_returns_the_report_and_the_csv(export_config: RunnerConfig) -> None:
    def write(out: Path) -> None:
        (out / "export_report.json").write_text(
            '{"rows": 2, "bytes": 9, "columns": ["n"]}', encoding="utf-8"
        )
        (out / "result.csv").write_text("n\n1\n2\n", encoding="utf-8")

    runner = Runner(export_config, execute=fake_ingest(write))
    result = runner.export(ExportRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result.status == "ok"
    assert result.report is not None and result.report.rows == 2 and result.report.error is None
    assert result.output_dir is not None and (result.output_dir / "result.csv").exists()


def test_an_export_error_comes_back_in_the_report(export_config: RunnerConfig) -> None:
    def write(out: Path) -> None:
        (out / "export_report.json").write_text(
            '{"rows": 0, "bytes": 0, "columns": [], "error": "the result has more than 10 rows"}',
            encoding="utf-8",
        )

    runner = Runner(export_config, execute=fake_ingest(write))
    result = runner.export(ExportRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result.report is not None and result.report.error == "the result has more than 10 rows"


def test_an_export_output_over_the_byte_cap_is_rejected(export_config: RunnerConfig) -> None:
    def write(out: Path) -> None:
        (out / "export_report.json").write_text(
            '{"rows": 1, "bytes": 1, "columns": ["n"]}', encoding="utf-8"
        )
        with (out / "result.csv").open("wb") as file:
            file.truncate(MAX_EXPORT_BYTES + 2 * 1_048_576)

    runner = Runner(export_config, execute=fake_ingest(write))
    result = runner.export(ExportRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result.status == "error"
    assert result.output_dir is None
    assert list(export_config.staging_root.iterdir()) == []


def test_export_timeouts_are_bounded() -> None:
    assert ExportRun(dataset_id="a", sql="SELECT 1").timeout_seconds == 120
    with pytest.raises(ValidationError):
        ExportRun(dataset_id="a", sql="SELECT 1", timeout_seconds=121)
