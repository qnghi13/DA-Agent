"""Raw ingestion through the Runner in a real sandbox container. Needs Docker."""

import duckdb
import openpyxl
import pytest

from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, TableSpec
from da_agent.runner.runner import BuildRun, IngestRun, Runner, RunnerConfig
from tests.security.test_sandbox_escape import IMAGE, _docker_ready, build_image

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> Runner:
    if not _docker_ready():
        pytest.skip("Docker is not available")
    build_image()
    root = tmp_path_factory.mktemp("ingest")
    for name in ("uploads", "staging", "datasets"):
        (root / name).mkdir()
    upload = root / "uploads" / "up_1"
    upload.mkdir()
    (upload / "sales.csv").write_text('Mã,Tổng\nD1,"22,000"\n', encoding="utf-8")
    book = openpyxl.Workbook()
    book.active.append(["SỔ NHẬP"])
    book.active.append([])
    book.active.append(["Ngày", "Số tiền"])
    book.active.append(["01/01/2025", "25000k"])
    book.save(upload / "nhap.xlsx")
    (upload / "README.md").write_text("notes", encoding="utf-8")
    for path in [upload, *upload.iterdir()]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    (root / "uploads" / "up_2").mkdir()
    (root / "uploads" / "up_2" / "other.csv").write_text("secret\n1\n", encoding="utf-8")
    return Runner(
        RunnerConfig(
            image=IMAGE,
            datasets_root=root / "datasets",
            uploads_root=root / "uploads",
            staging_root=root / "staging",
            ingest_memory_mb=512,
        )
    )


def test_an_upload_is_parsed_in_the_sandbox(runner: Runner) -> None:
    result = runner.ingest(IngestRun(upload_id="up_1", timeout_seconds=120))

    assert result.status == "ok", result.stderr
    assert result.report is not None and result.output_dir is not None
    assert sorted(t.name for t in result.report.tables) == ["nhap", "sales"]
    assert {f.path: f.status for f in result.report.files}["README.md"] == "document"
    assert (result.output_dir / "raw.duckdb").is_file()
    assert not (result.output_dir / "_work").exists()


def test_the_parser_sees_only_its_own_upload(runner: Runner) -> None:
    result = runner.ingest(IngestRun(upload_id="up_1", timeout_seconds=120))

    assert result.report is not None
    assert "other.csv" not in result.report.model_dump_json()


def test_unreadable_parts_of_an_upload_are_reported(runner: Runner) -> None:
    assert runner.config.uploads_root is not None
    upload = runner.config.uploads_root / "up_locked"
    upload.mkdir(mode=0o755)
    (upload / "ok.csv").write_text("a\n1\n", encoding="utf-8")
    (upload / "ok.csv").chmod(0o644)
    # The sandbox user cannot read this directory.
    (upload / "locked").mkdir(mode=0o700)
    (upload / "locked" / "x.csv").write_text("a\n1\n", encoding="utf-8")

    result = runner.ingest(IngestRun(upload_id="up_locked", timeout_seconds=60))

    assert result.status == "ok", result.stderr
    assert result.report is not None
    assert [t.name for t in result.report.tables] == ["ok"]
    assert any("locked" in warning for warning in result.report.warnings)


def test_a_spec_builds_clean_tables_in_the_sandbox(runner: Runner) -> None:
    ingested = runner.ingest(IngestRun(upload_id="up_1", timeout_seconds=120))
    assert ingested.output_dir is not None
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="sales",
                name="sales",
                columns=[ColumnSpec(source="Tổng", name="total", type="money")],
            )
        ]
    )

    built = runner.build(BuildRun(ingest_id=ingested.output_dir.name, spec=spec))

    assert built.status == "ok", built.stderr
    assert built.report is not None and built.output_dir is not None
    database = str(built.output_dir / "dataset.duckdb")
    with duckdb.connect(database, read_only=True) as connection:
        assert connection.execute("SELECT ma, total FROM sales").fetchall() == [("D1", 22000)]
