"""The Runner over HTTP (ADR 0024): the service, and the client the worker uses. No Docker."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from da_agent.ingestion.report import IngestReport
from da_agent.runner.client import RunnerClient
from da_agent.runner.runner import (
    BuildRun,
    IngestResult,
    IngestRun,
    SqlResult,
    SqlRun,
)
from da_agent.runner.service import create_runner_app

TOKEN = "t" * 40
# The client sets a timeout per request; TestClient ignores it, which is fine here.
pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")


class FakeRunner:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.requests: list[object] = []
        self.output_dir: Path | None = None

    def query(self, request: SqlRun) -> SqlResult:
        self.requests.append(request)
        if request.dataset_id == "missing":
            raise ValueError("unknown dataset 'missing'")
        return SqlResult(status="ok", columns=["n"], rows=[[1]])

    def ingest(self, request: IngestRun) -> IngestResult:
        self.requests.append(request)
        out = self.output_dir or self.staging / "da-sbx-1"
        return IngestResult(
            status="ok",
            exit_code=0,
            stderr="",
            report=IngestReport(tables=[], warnings=["w"]),
            output_dir=out,
            duration_seconds=1.0,
        )

    def build(self, request: BuildRun):  # pragma: no cover - same path as ingest
        raise NotImplementedError


@pytest.fixture
def runner(tmp_path: Path) -> FakeRunner:
    return FakeRunner(tmp_path)


@pytest.fixture
def http(runner: FakeRunner) -> TestClient:
    return TestClient(create_runner_app(runner, TOKEN), base_url="http://runner")


def client(http: httpx.Client, staging: Path, token: str = TOKEN) -> RunnerClient:
    return RunnerClient("http://runner", token, staging_root=staging, http=http)


@pytest.mark.security
@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Basic {TOKEN}", TOKEN])
def test_requests_without_the_token_are_refused(http: TestClient, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    response = http.post("/v1/query", json={"dataset_id": "a", "sql": "SELECT 1"}, headers=headers)

    assert response.status_code == 401


@pytest.mark.security
def test_requests_cannot_add_mounts_or_flags(http: TestClient) -> None:
    body = {"dataset_id": "a", "sql": "SELECT 1", "mounts": ["/:/host"], "image": "alpine"}
    response = http.post("/v1/query", json=body, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422


@pytest.mark.security
def test_python_runs_are_not_exposed(http: TestClient) -> None:
    body = {"dataset_id": "a", "code": "print(1)"}
    response = http.post("/v1/python", json=body, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 404


def test_a_query_goes_through_the_service(
    http: TestClient, runner: FakeRunner, tmp_path: Path
) -> None:
    result = client(http, tmp_path).query(SqlRun(dataset_id="shop_1", sql="SELECT 1"))

    assert result == SqlResult(status="ok", columns=["n"], rows=[[1]])
    assert runner.requests == [SqlRun(dataset_id="shop_1", sql="SELECT 1")]


def test_an_unknown_dataset_raises_as_the_local_runner_does(
    http: TestClient, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        client(http, tmp_path).query(SqlRun(dataset_id="missing", sql="SELECT 1"))


def test_an_ingest_returns_the_report_and_the_shared_output_directory(
    http: TestClient, tmp_path: Path
) -> None:
    result = client(http, tmp_path).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "ok"
    assert result.report is not None and result.report.warnings == ["w"]
    assert result.output_dir == tmp_path / "da-sbx-1"


@pytest.mark.security
def test_an_output_directory_outside_staging_is_rejected(
    http: TestClient, runner: FakeRunner, tmp_path: Path
) -> None:
    runner.output_dir = Path("/etc")

    result = client(http, tmp_path).ingest(IngestRun(upload_id="up_1"))

    assert result.status == "error"
    assert result.output_dir is None and result.report is None


def test_a_runner_that_cannot_be_reached_is_an_error_result(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://runner")
    remote = client(http, tmp_path)

    query = remote.query(SqlRun(dataset_id="shop_1", sql="SELECT 1"))
    ingest = remote.ingest(IngestRun(upload_id="up_1"))

    assert query.status == "error" and query.error is not None
    assert "unavailable" in query.error
    assert ingest.status == "error" and ingest.output_dir is None


def test_a_wrong_token_is_a_configuration_error_not_a_result(
    http: TestClient, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError, match="token"):
        client(http, tmp_path, token="x" * 40).query(SqlRun(dataset_id="a", sql="SELECT 1"))


def test_the_health_check_needs_no_token(http: TestClient) -> None:
    assert http.get("/healthz").status_code == 200


def test_the_service_refuses_to_start_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from da_agent.runner.__main__ import main

    monkeypatch.delenv("DA_AGENT_RUNNER_TOKEN", raising=False)
    monkeypatch.setenv("DA_AGENT_DATA_DIR", "/data")

    assert main([]) == 2
    assert "DA_AGENT_RUNNER_TOKEN" in capsys.readouterr().err
