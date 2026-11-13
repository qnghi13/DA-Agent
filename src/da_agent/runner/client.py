"""The worker's side of the Runner service (ADR 0024). Same methods as the in-process Runner."""

from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from da_agent.runner.runner import (
    STARTUP_GRACE_SECONDS,
    BuildResult,
    BuildRun,
    ExportResult,
    ExportRun,
    IngestResult,
    IngestRun,
    JobResult,
    SqlResult,
    SqlRun,
)

UNAVAILABLE = "the sandbox service is unavailable; try again later"
# Time for the HTTP round trip on top of the sandbox's own limit.
_NETWORK_SECONDS = 30


class RunnerClient:
    def __init__(
        self, url: str, token: str, *, staging_root: Path, http: httpx.Client | None = None
    ) -> None:
        self._http = http or httpx.Client(base_url=url)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._staging = staging_root.resolve()

    def query(self, request: SqlRun) -> SqlResult:
        body = self._post("/v1/query", request, request.timeout_seconds)
        if body is None:
            return SqlResult(status="error", error=UNAVAILABLE)
        try:
            return SqlResult.model_validate_json(body)
        except ValidationError:
            return SqlResult(status="error", error="the sandbox service returned no result")

    def ingest(self, request: IngestRun) -> IngestResult:
        return self._job("/v1/ingest", request, IngestResult)

    def build(self, request: BuildRun) -> BuildResult:
        return self._job("/v1/build", request, BuildResult)

    def export(self, request: ExportRun) -> ExportResult:
        return self._job("/v1/export", request, ExportResult)

    def _job[ResultT: JobResult](
        self, path: str, request: IngestRun | BuildRun | ExportRun, model: type[ResultT]
    ) -> ResultT:
        def failure(message: str) -> ResultT:
            return model(
                status="error",
                exit_code=None,
                stderr=message,
                report=None,
                output_dir=None,
                duration_seconds=0.0,
            )

        body = self._post(path, request, request.timeout_seconds)
        if body is None:
            return failure(UNAVAILABLE)
        try:
            result = model.model_validate_json(body)
        except ValidationError:
            return failure("the sandbox service returned no result")
        out = result.output_dir
        if out is not None and out.resolve().parent != self._staging:
            # Both sides mount the same volume at the same path; anything else is not ours to use.
            return failure("the sandbox service returned an output outside staging")
        return result

    def _post(self, path: str, request: BaseModel, timeout_seconds: int) -> bytes | None:
        """The response body, or None when the service cannot be reached."""
        timeout = timeout_seconds + STARTUP_GRACE_SECONDS + _NETWORK_SECONDS
        try:
            response = self._http.post(
                path,
                content=request.model_dump_json(),
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=timeout,
            )
        except httpx.HTTPError:
            return None
        if response.status_code == 401:
            raise RuntimeError("the sandbox service rejected the runner token")
        if response.status_code == 404:
            raise ValueError(_detail(response))
        if response.status_code >= 500:
            return None
        if response.status_code != 200:
            raise RuntimeError(f"the sandbox service refused the request: {_detail(response)}")
        return response.content


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail if detail is not None else response.status_code)[:500]
