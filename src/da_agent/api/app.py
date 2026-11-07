"""The HTTP API. Handlers authenticate, check the workspace, read or write Postgres and the uploads
directory, and queue jobs. They never call a model or run generated code; the worker does.

Every dataset and run is looked up with the caller's workspace, and anything outside it is "not
found", so ids from another workspace reveal nothing. See docs/adr/0020.
"""

import datetime as dt
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from da_agent.agent.answer import result_from_json
from da_agent.reports.html import CONTENT_SECURITY_POLICY, render_report
from da_agent.storage import audit
from da_agent.storage.datasets import (
    DatasetNotFoundError,
    DatasetRecord,
    DatasetStateError,
    DatasetStore,
    InvalidSpecError,
)
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.keys import KEY_PATTERN, KeyIdentity, authenticate
from da_agent.storage.uploads import UploadRejectedError, storage_name

CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ApiSettings:
    database_url: str
    uploads_root: Path
    # For tests: confine every query to one Postgres schema.
    schema: str | None = None
    max_files: int = 50
    max_file_bytes: int = 200 * 1024 * 1024


class ApproveRequest(BaseModel):
    spec: dict[str, Any] | None = Field(
        default=None, description="An edited spec. Omit it to approve the proposed one."
    )


class RunRequest(BaseModel):
    dataset_id: str
    question: str = Field(min_length=1, max_length=2000)
    as_of: dt.date | None = Field(
        default=None, description="The date the question is asked on. Defaults to today (UTC)."
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise _not_found() from error


def _dataset_view(record: DatasetRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "spec": record.spec,
        "summary": record.summary,
        "error": record.error,
        "files": record.files,
        "documents": len(record.documents),
    }


def _run_view(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "dataset_id": job.payload.get("dataset_id"),
        "question": job.payload.get("question"),
        "as_of": job.payload.get("as_of"),
        "status": job.status,
        "attempts": job.attempts,
        "error": job.last_error,
        "result": job.result,
    }


def _save(upload: UploadFile, path: Path, limit: int) -> int:
    """Copy an upload to path in chunks, refusing it once it passes limit bytes."""
    written = 0
    with path.open("xb") as out:
        while chunk := upload.file.read(CHUNK_BYTES):
            written += len(chunk)
            if written > limit:
                raise HTTPException(status_code=413, detail=f"{upload.filename!r} is too large")
            out.write(chunk)
    return written


def _settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def database(request: Request) -> Iterator[psycopg.Connection]:
    settings = _settings(request)
    with connect(settings.database_url, schema=settings.schema) as conn:
        yield conn


Db = Annotated[psycopg.Connection, Depends(database)]


def identity(request: Request, conn: Db) -> KeyIdentity:
    scheme, _, key = request.headers.get("authorization", "").partition(" ")
    key = key.strip()
    found = authenticate(conn, key) if scheme.lower() == "bearer" else None
    if found is None:
        # Only a well-formed prefix is logged: a pasted secret of another kind is not.
        match = KEY_PATTERN.fullmatch(key)
        detail = {"path": request.url.path, "prefix": match.group(1) if match else None}
        audit.record(conn, "auth.failed", detail=detail)
        raise HTTPException(
            status_code=401, detail="a valid API key is required",
            headers={"WWW-Authenticate": "Bearer"},
        )  # fmt: skip
    return found


Caller = Annotated[KeyIdentity, Depends(identity)]
Settings = Annotated[ApiSettings, Depends(_settings)]
router = APIRouter(prefix="/v1")


def log(conn: psycopg.Connection, caller: KeyIdentity, action: str, target: str, **detail: Any):
    audit.record(
        conn, action, workspace_id=caller.workspace_id, key_id=caller.key_id,
        target=target, detail=detail,
    )  # fmt: skip


def create_app(settings: ApiSettings) -> FastAPI:
    app = FastAPI(title="DA-Agent", version="0.1.0")
    app.state.settings = settings
    app.include_router(router)
    return app


@router.post("/datasets", status_code=202)
def upload_dataset(
    conn: Db,
    caller: Caller,
    settings: Settings,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    files: Annotated[list[UploadFile], File()],
    description: Annotated[str, Form(max_length=20000)] = "",
) -> dict[str, Any]:
    """Upload data files and their documents. Ingestion runs as a job; poll the dataset."""
    if len(files) > settings.max_files:
        raise HTTPException(status_code=413, detail=f"at most {settings.max_files} files")
    names: list[str] = []
    try:
        for upload in files:
            names.append(storage_name(upload.filename or "", set(names)))
    except UploadRejectedError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    dataset_id = uuid.uuid4()
    folder = settings.uploads_root / str(dataset_id)
    folder.mkdir(parents=True)
    try:
        stored = [
            {
                "name": upload.filename,
                "stored_as": stored_name,
                "bytes": _save(upload, folder / stored_name, settings.max_file_bytes),
            }
            for upload, stored_name in zip(files, names, strict=True)
        ]
        with conn.transaction():
            DatasetStore(conn).create(
                caller.workspace_id, name, description, dataset_id=dataset_id, files=stored
            )
            JobQueue(conn).enqueue(caller.workspace_id, "ingest", {"dataset_id": str(dataset_id)})
            log(conn, caller, "dataset.upload", str(dataset_id), files=len(stored))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"id": str(dataset_id), "status": "uploaded", "files": stored}


@router.get("/datasets")
def list_datasets(conn: Db, caller: Caller) -> dict[str, Any]:
    return {"datasets": DatasetStore(conn).in_workspace(caller.workspace_id)}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    record = DatasetStore(conn).get(_uuid(dataset_id), caller.workspace_id)
    if record is None:
        raise _not_found()
    return _dataset_view(record)


@router.post("/datasets/{dataset_id}/approve")
def approve_dataset(
    dataset_id: str, body: ApproveRequest, conn: Db, caller: Caller
) -> dict[str, Any]:
    """Approve the proposed spec, or send an edited one to be built."""
    key = _uuid(dataset_id)
    try:
        status = DatasetStore(conn).approve(caller.workspace_id, key, body.spec)
    except DatasetNotFoundError as error:
        raise _not_found() from error
    except InvalidSpecError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DatasetStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log(conn, caller, "dataset.approve", dataset_id, edited=body.spec is not None, status=status)
    return {"id": dataset_id, "status": status}


@router.post("/runs", status_code=202)
def create_run(body: RunRequest, conn: Db, caller: Caller) -> dict[str, Any]:
    """Ask a question about a ready dataset. The run is a job; poll it or stream its events."""
    record = DatasetStore(conn).get(_uuid(body.dataset_id), caller.workspace_id)
    if record is None:
        raise _not_found()
    if record.status != "ready":
        raise HTTPException(status_code=409, detail=f"the dataset is {record.status}, not ready")
    as_of = (body.as_of or dt.datetime.now(dt.UTC).date()).isoformat()
    payload = {
        "dataset_id": str(record.id),
        "question": body.question,
        "as_of": as_of,
        "charts": True,
    }
    with conn.transaction():
        run_id = JobQueue(conn).enqueue(caller.workspace_id, "run", payload)
        log(conn, caller, "run.create", str(run_id), dataset_id=str(record.id))
    return {"id": str(run_id), "status": "queued"}


def run_job(run_id: str, conn: psycopg.Connection, caller: KeyIdentity) -> Job:
    job = JobQueue(conn).get(_uuid(run_id), caller.workspace_id)
    if job is None or job.kind != "run":
        raise _not_found()
    return job


@router.get("/runs/{run_id}")
def get_run(run_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    return _run_view(run_job(run_id, conn, caller))


@router.get("/runs/{run_id}/report", response_class=HTMLResponse)
def get_report(run_id: str, conn: Db, caller: Caller) -> HTMLResponse:
    """The run as a standalone HTML report, where every number links to its query cell."""
    job = run_job(run_id, conn, caller)
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(status_code=409, detail=f"the run is {job.status}")
    page = render_report(
        result_from_json(job.result),
        question=str(job.payload.get("question", "")),
        as_of=job.payload.get("as_of"),
    )
    headers = {
        "Content-Security-Policy": CONTENT_SECURITY_POLICY,
        "X-Content-Type-Options": "nosniff",
    }
    return HTMLResponse(page, headers=headers)
