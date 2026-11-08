"""The Runner over HTTP, for deployments where it is the only holder of the Docker socket.

See docs/adr/0024. The service accepts the same typed requests as the in-process Runner, so a
caller still cannot choose an image, a mount, or a flag. Python runs are not exposed.
"""

import hmac

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from da_agent.runner.runner import (
    BuildResult,
    BuildRun,
    IngestResult,
    IngestRun,
    SandboxRunner,
    SqlResult,
    SqlRun,
)


def create_runner_app(runner: SandboxRunner, token: str) -> FastAPI:
    expected = f"Bearer {token}".encode()

    def authorized(request: Request) -> None:
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="missing or wrong runner token")

    app = FastAPI(title="DA-Agent Runner", docs_url=None, redoc_url=None, openapi_url=None)
    auth = [Depends(authorized)]

    @app.exception_handler(ValueError)
    def unknown_input(_: Request, error: ValueError) -> JSONResponse:
        # The Runner raises ValueError for ids that name no upload, ingest output, or dataset.
        return JSONResponse(status_code=404, content={"detail": str(error)[:500]})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/query", dependencies=auth)
    def query(request: SqlRun) -> SqlResult:
        return runner.query(request)

    @app.post("/v1/ingest", dependencies=auth)
    def ingest(request: IngestRun) -> IngestResult:
        return runner.ingest(request)

    @app.post("/v1/build", dependencies=auth)
    def build(request: BuildRun) -> BuildResult:
        return runner.build(request)

    return app
