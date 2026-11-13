"""The HTTP API. Handlers authenticate, check the workspace, read or write Postgres and the uploads
directory, and queue jobs. They never call a model or run generated code; the worker does.

Every dataset and run is looked up with the caller's workspace, and anything outside it is "not
found", so ids from another workspace reveal nothing. See docs/adr/0020.
"""

import datetime as dt
import json
import math
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from da_agent.agent.answer import result_from_json
from da_agent.reports.html import CONTENT_SECURITY_POLICY, render_report
from da_agent.storage import audit, ops
from da_agent.storage.datasets import (
    DatasetNotFoundError,
    DatasetRecord,
    DatasetStateError,
    DatasetStore,
    InvalidSpecError,
)
from da_agent.storage.db import connect
from da_agent.storage.events import RunEvent, RunEvents
from da_agent.storage.exports import (
    CHANGED_DATASET,
    dataset_changed_since_run,
    export_file,
    is_expired,
)
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.keys import KEY_PATTERN, authenticate
from da_agent.storage.ledger import daily_budget, spent_today
from da_agent.storage.limits import RateLimiter
from da_agent.storage.memory import (
    MAX_SQL,
    MAX_TEXT,
    ConversationStore,
    MemoryEntry,
    MemoryNotFoundError,
    MemoryStateError,
    MemoryStore,
    memory_snapshot,
)
from da_agent.storage.sessions import (
    LINK_TTL,
    MAX_AGE,
    create_login_link,
    create_session,
    find_session,
    redeem_login_link,
    revoke_session,
)
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
    # Event streams: how often to look for new events, when to send a keep-alive comment, and
    # how long one connection lasts before the client reconnects with Last-Event-ID.
    events_poll_seconds: float = 0.5
    events_keepalive_seconds: float = 15.0
    events_max_seconds: float = 300.0
    # Limits (ADR 0022): requests per key, new jobs per workspace, failed keys per client address,
    # and the model budget a workspace may spend per day unless it has its own.
    requests_per_minute: int = 300
    jobs_per_minute: int = 30
    failed_auth_per_minute: int = 20
    daily_budget_usd: float = 5.0
    # Trace viewer for operators (ADR 0023). Unset, runs carry no trace links.
    trace_ui_url: str | None = None
    # Where browsers open the UI (ADR 0025): login links point here, cookie requests that change
    # things must come from this origin, and https makes the cookie Secure.
    public_url: str = "http://127.0.0.1:8080"
    # Result exports (docs/design/2026-11-14-result-export.md). Unset, they sit beside uploads.
    exports_root: Path | None = None

    @property
    def exports_dir(self) -> Path:
        return self.exports_root or self.uploads_root.parent / "exports"


MAX_RUNS_PER_PAGE = 50


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
    conversation_id: str | None = Field(
        default=None,
        description="A conversation of the same dataset; the run reads its earlier turns.",
    )


class MemoryRequest(BaseModel):
    kind: Literal["correction", "alias", "example"]
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    sql: str | None = Field(default=None, max_length=MAX_SQL, description="Required for examples.")
    source_run_id: str | None = Field(default=None, description="The run this note corrects.")


class MemoryEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    sql: str | None = Field(default=None, max_length=MAX_SQL)


class ConversationRequest(BaseModel):
    dataset_id: str


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


def _trace_links(trace_ids: list[str], trace_ui_url: str | None) -> list[dict[str, Any]]:
    if not trace_ui_url:
        return []
    return [
        {"attempt": attempt, "url": f"{trace_ui_url}/redirects/traces/{trace_id}"}
        for attempt, trace_id in enumerate(trace_ids, start=1)
    ]


def _run_view(job: Job, trace_ui_url: str | None = None) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "dataset_id": job.payload.get("dataset_id"),
        "question": job.payload.get("question"),
        "as_of": job.payload.get("as_of"),
        "conversation_id": job.payload.get("conversation_id"),
        "status": job.status,
        "attempts": job.attempts,
        "error": job.last_error,
        "result": job.result,
        "traces": _trace_links(job.trace_ids, trace_ui_url),
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


def _too_many(wait: float, detail: str) -> HTTPException:
    return HTTPException(
        status_code=429, detail=detail, headers={"Retry-After": str(math.ceil(wait))}
    )


@dataclass(frozen=True)
class Principal:
    """Whoever sent the request: a key or a member session (with a workspace), or the operator."""

    role: str
    workspace_id: UUID | None
    key_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True)
class Member:
    """A caller inside one workspace. Every workspace endpoint works with this."""

    workspace_id: UUID
    key_id: UUID | None = None
    session_id: UUID | None = None


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _secure(settings: ApiSettings) -> bool:
    return urlsplit(settings.public_url).scheme == "https"


def cookie_name(settings: ApiSettings) -> str:
    """`__Host-` makes browsers refuse the cookie unless it is Secure, host-only, and Path=/."""
    return "__Host-da_session" if _secure(settings) else "da_session"


def _require_origin(request: Request, settings: ApiSettings) -> None:
    """Cookie-authenticated changes, and sign-ins, must come from our own pages (ADR 0025)."""
    if request.headers.get("origin") != _origin(settings.public_url):
        raise HTTPException(status_code=403, detail="this request must come from the DA-Agent UI")


def _refuse(
    request: Request, conn: psycopg.Connection, settings: ApiSettings, detail: dict[str, Any]
) -> HTTPException:
    """Count a failed credential against the client address, log it, and build the 401."""
    address = request.client.host if request.client else "unknown"
    RateLimiter(conn).hit(
        f"authfail:{address}", limit=settings.failed_auth_per_minute, window_seconds=60
    )
    audit.record(conn, "auth.failed", detail={"path": request.url.path, **detail})
    return HTTPException(
        status_code=401, detail="sign in, or send a valid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )  # fmt: skip


def _not_blocked(request: Request, conn: psycopg.Connection, settings: ApiSettings) -> None:
    address = request.client.host if request.client else "unknown"
    wait = RateLimiter(conn).exceeded(
        f"authfail:{address}", limit=settings.failed_auth_per_minute, window_seconds=60
    )
    if wait is not None:
        raise _too_many(wait, "too many failed sign-ins or keys from this address")


def principal(request: Request, conn: Db) -> Principal:
    """Authenticate a bearer key or, without one, the session cookie."""
    settings = _settings(request)
    _not_blocked(request, conn, settings)
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, key = header.partition(" ")
        key = key.strip()
        found = authenticate(conn, key) if scheme.lower() == "bearer" else None
        if found is None:
            # Only a well-formed prefix is logged: a pasted secret of another kind is not.
            match = KEY_PATTERN.fullmatch(key)
            raise _refuse(request, conn, settings, {"prefix": match.group(1) if match else None})
        caller = Principal("member", found.workspace_id, key_id=found.key_id)
        bucket = f"key:{found.key_id}"
    else:
        token = request.cookies.get(cookie_name(settings))
        if token is None:
            raise _refuse(request, conn, settings, {"prefix": None})
        session = find_session(conn, token)
        if session is None:
            raise _refuse(request, conn, settings, {"session": "expired or unknown"})
        if request.method in UNSAFE_METHODS:
            _require_origin(request, settings)
        caller = Principal(session.role, session.workspace_id, session_id=session.session_id)
        bucket = f"session:{session.session_id}"
    wait = RateLimiter(conn).hit(bucket, limit=settings.requests_per_minute, window_seconds=60)
    if wait is not None:
        raise _too_many(wait, "too many requests for this key or session")
    return caller


Who = Annotated[Principal, Depends(principal)]


def identity(caller: Who) -> Member:
    """Workspace endpoints. The operator has no workspace, so they do not exist for it."""
    if caller.workspace_id is None:
        raise _not_found()
    return Member(caller.workspace_id, key_id=caller.key_id, session_id=caller.session_id)


def operator(caller: Who) -> Principal:
    """Operator endpoints. Members and keys get the same 404 as for anything unknown."""
    if caller.role != "operator":
        raise _not_found()
    return caller


def _may_start_job(
    conn: psycopg.Connection, caller: Member, settings: ApiSettings, *, calls_model: bool = True
) -> None:
    """New jobs have their own rate per workspace, and jobs that call a model stop when the day's
    model budget is spent."""
    wait = RateLimiter(conn).hit(
        f"jobs:{caller.workspace_id}", limit=settings.jobs_per_minute, window_seconds=60
    )
    if wait is not None:
        raise _too_many(wait, "too many new jobs for this workspace")
    if not calls_model:
        return
    limit = daily_budget(conn, caller.workspace_id, settings.daily_budget_usd)
    if spent_today(conn, caller.workspace_id) >= limit:
        midnight = dt.datetime.combine(
            dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1), dt.time(), dt.UTC
        )
        wait = (midnight - dt.datetime.now(dt.UTC)).total_seconds()
        raise _too_many(wait, f"the workspace has spent its daily model budget (${limit:.2f})")


Caller = Annotated[Member, Depends(identity)]
Operator = Annotated[Principal, Depends(operator)]
Settings = Annotated[ApiSettings, Depends(_settings)]
router = APIRouter(prefix="/v1")


def log(
    conn: psycopg.Connection, caller: Member | Principal, action: str, target: str, **detail: Any
) -> None:
    audit.record(
        conn, action, workspace_id=caller.workspace_id, key_id=caller.key_id,
        session_id=caller.session_id, target=target, detail=detail,
    )  # fmt: skip


# Sign-in (ADR 0025)


class RedeemRequest(BaseModel):
    token: str = Field(max_length=200, description="The token after `#token=` in a login link.")


class LinkRequest(BaseModel):
    role: Literal["member", "operator"] | None = Field(
        default=None, description="Defaults to the caller's own role."
    )


def _me(conn: psycopg.Connection, role: str, workspace_id: UUID | None) -> dict[str, Any]:
    name = None
    if workspace_id is not None:
        row = conn.execute("SELECT name FROM workspaces WHERE id = %s", (workspace_id,)).fetchone()
        name = row[0] if row else None
    return {
        "role": role,
        "workspace_id": str(workspace_id) if workspace_id else None,
        "workspace_name": name,
    }


def _start_session(
    conn: psycopg.Connection,
    settings: ApiSettings,
    role: str,
    workspace_id: UUID | None,
    **detail: Any,
) -> JSONResponse:
    token, session_id = create_session(conn, role=role, workspace_id=workspace_id)
    audit.record(
        conn, "auth.sign_in", workspace_id=workspace_id, session_id=session_id,
        detail={"role": role, **detail},
    )  # fmt: skip
    response = JSONResponse(_me(conn, role, workspace_id))
    response.set_cookie(
        cookie_name(settings), token, max_age=int(MAX_AGE.total_seconds()), path="/",
        secure=_secure(settings), httponly=True, samesite="strict",
    )  # fmt: skip
    return response


@router.post("/auth/redeem")
def redeem(body: RedeemRequest, request: Request, conn: Db, settings: Settings) -> JSONResponse:
    """Exchange a one-time login link for a session cookie."""
    _not_blocked(request, conn, settings)
    _require_origin(request, settings)
    grant = redeem_login_link(conn, body.token.strip())
    if grant is None:
        raise _refuse(request, conn, settings, {"link": "used, expired, or unknown"})
    return _start_session(conn, settings, grant.role, grant.workspace_id, via="link")


@router.post("/auth/session")
def key_to_session(request: Request, conn: Db, caller: Who, settings: Settings) -> JSONResponse:
    """Exchange an API key, sent once as a bearer token, for a session cookie."""
    _require_origin(request, settings)
    if caller.key_id is None or caller.workspace_id is None:
        raise HTTPException(status_code=400, detail="send an API key as a bearer token")
    return _start_session(conn, settings, "member", caller.workspace_id, via="key")


@router.get("/auth/me")
def me(conn: Db, caller: Who) -> dict[str, Any]:
    return _me(conn, caller.role, caller.workspace_id)


@router.post("/auth/links", status_code=201)
def create_link(body: LinkRequest, conn: Db, caller: Who, settings: Settings) -> dict[str, Any]:
    """A one-time link that signs another browser in, with the caller's own role."""
    role = body.role or caller.role
    if role != caller.role:
        raise _not_found()
    token = create_login_link(conn, role=role, workspace_id=caller.workspace_id)
    log(conn, caller, "auth.link", role)
    return {
        "url": f"{settings.public_url.rstrip('/')}/login#token={token}",
        "expires_in_seconds": int(LINK_TTL.total_seconds()),
    }


@router.post("/auth/logout", status_code=204)
def logout(request: Request, conn: Db, settings: Settings) -> Response:
    """End this browser's session. Without one there is nothing to end."""
    token = request.cookies.get(cookie_name(settings))
    session = find_session(conn, token) if token else None
    if session is not None:
        _require_origin(request, settings)
        revoke_session(conn, session.session_id)
        audit.record(
            conn, "auth.sign_out", workspace_id=session.workspace_id,
            session_id=session.session_id,
        )  # fmt: skip
    response = Response(status_code=204)
    response.delete_cookie(
        cookie_name(settings), path="/", secure=_secure(settings), httponly=True,
        samesite="strict",
    )  # fmt: skip
    return response


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
    _may_start_job(conn, caller, settings)
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
def create_run(body: RunRequest, conn: Db, caller: Caller, settings: Settings) -> dict[str, Any]:
    """Ask a question about a ready dataset. The run is a job; poll it or stream its events."""
    record = DatasetStore(conn).get(_uuid(body.dataset_id), caller.workspace_id)
    if record is None:
        raise _not_found()
    if record.status != "ready":
        raise HTTPException(status_code=409, detail=f"the dataset is {record.status}, not ready")
    conversation = None
    if body.conversation_id is not None:
        found = ConversationStore(conn).get(caller.workspace_id, _uuid(body.conversation_id))
        if found is None or found["dataset_id"] != record.id:
            raise _not_found()
        conversation = str(found["id"])
    _may_start_job(conn, caller, settings)
    as_of = (body.as_of or dt.datetime.now(dt.UTC).date()).isoformat()
    payload: dict[str, Any] = {
        "dataset_id": str(record.id),
        "question": body.question,
        "as_of": as_of,
        "charts": True,
    }
    if conversation is not None:
        payload["conversation_id"] = conversation
    with conn.transaction():
        # The notes the run reads are fixed now, so a resume reads the same ones (ADR 0030).
        payload |= memory_snapshot(conn, caller.workspace_id, record.id)
        run_id = JobQueue(conn).enqueue(caller.workspace_id, "run", payload)
        log(conn, caller, "run.create", str(run_id), dataset_id=str(record.id))
    return {"id": str(run_id), "status": "queued"}


# Conversations and memory (ADR 0030)


def _author(caller: Member) -> str:
    if caller.key_id is not None:
        return f"key:{caller.key_id}"
    return f"session:{caller.session_id}"


def _memory_view(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "kind": entry.kind,
        "text": entry.text,
        "sql": entry.sql,
        "status": entry.status,
        "author": entry.author,
        "source_run_id": str(entry.source_run_id) if entry.source_run_id else None,
        "superseded_by": str(entry.superseded_by) if entry.superseded_by else None,
    }


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationRequest, conn: Db, caller: Caller) -> dict[str, Any]:
    """Start a conversation about one dataset. Runs that name it read its earlier turns."""
    try:
        conversation_id = ConversationStore(conn).create(
            caller.workspace_id, _uuid(body.dataset_id)
        )
    except MemoryNotFoundError as error:
        raise _not_found() from error
    log(conn, caller, "conversation.create", str(conversation_id), dataset_id=body.dataset_id)
    return {"id": str(conversation_id)}


@router.get("/datasets/{dataset_id}/memory")
def list_memory(dataset_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    """Every note of the dataset, for review: proposed, approved, rejected, and replaced."""
    key = _uuid(dataset_id)
    if DatasetStore(conn).get(key, caller.workspace_id) is None:
        raise _not_found()
    entries = MemoryStore(conn).entries(caller.workspace_id, key)
    return {"memory": [_memory_view(entry) for entry in entries]}


@router.post("/datasets/{dataset_id}/memory", status_code=201)
def write_memory(dataset_id: str, body: MemoryRequest, conn: Db, caller: Caller) -> dict[str, Any]:
    """A note the caller writes is approved at once, and later runs of the dataset read it."""
    source = None
    if body.source_run_id is not None:
        source = run_job(body.source_run_id, conn, caller).id
    try:
        entry_id = MemoryStore(conn).write(
            caller.workspace_id, _uuid(dataset_id), body.kind, body.text, sql=body.sql,
            author=_author(caller), source_run_id=source,
        )  # fmt: skip
    except MemoryNotFoundError as error:
        raise _not_found() from error
    except MemoryStateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log(conn, caller, "memory.create", str(entry_id), dataset_id=dataset_id, kind=body.kind)
    return {"id": str(entry_id), "status": "approved"}


def _change_memory(conn: psycopg.Connection, caller: Member, action: str, memory_id: str) -> None:
    store, key = MemoryStore(conn), _uuid(memory_id)
    change = {"approve": store.approve, "reject": store.reject, "delete": store.delete}[action]
    try:
        change(caller.workspace_id, key)
    except MemoryNotFoundError as error:
        raise _not_found() from error
    except MemoryStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log(conn, caller, f"memory.{action}", memory_id)


@router.post("/memory/{memory_id}/approve")
def approve_memory(memory_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    """Approve a suggested note, so later runs read it."""
    _change_memory(conn, caller, "approve", memory_id)
    return {"id": memory_id, "status": "approved"}


@router.post("/memory/{memory_id}/reject")
def reject_memory(memory_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    _change_memory(conn, caller, "reject", memory_id)
    return {"id": memory_id, "status": "rejected"}


@router.patch("/memory/{memory_id}")
def edit_memory(
    memory_id: str, body: MemoryEditRequest, conn: Db, caller: Caller
) -> dict[str, Any]:
    """Replace a note with an approved one; the old one stays in the history."""
    try:
        new_id = MemoryStore(conn).edit(
            caller.workspace_id, _uuid(memory_id), body.text, sql=body.sql,
            author=_author(caller),
        )  # fmt: skip
    except MemoryNotFoundError as error:
        raise _not_found() from error
    except MemoryStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    log(conn, caller, "memory.edit", memory_id, replaced_by=str(new_id))
    return {"id": str(new_id), "replaces": memory_id, "status": "approved"}


@router.delete("/memory/{memory_id}", status_code=204)
def delete_memory(memory_id: str, conn: Db, caller: Caller) -> Response:
    _change_memory(conn, caller, "delete", memory_id)
    return Response(status_code=204)


def run_job(run_id: str, conn: psycopg.Connection, caller: Member) -> Job:
    job = JobQueue(conn).get(_uuid(run_id), caller.workspace_id)
    if job is None or job.kind != "run":
        raise _not_found()
    return job


@router.get("/runs")
def list_runs(
    conn: Db,
    caller: Caller,
    dataset_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS_PER_PAGE)] = 20,
    before: dt.datetime | None = None,
) -> dict[str, Any]:
    """A dataset's runs, newest first. Pass next_before as before to read the next page."""
    record = DatasetStore(conn).get(_uuid(dataset_id), caller.workspace_id)
    if record is None:
        raise _not_found()
    runs = JobQueue(conn).runs_of_dataset(
        caller.workspace_id, str(record.id), limit=limit, before=before
    )
    return {
        "runs": [
            {
                "id": str(run.id),
                "question": run.question,
                "as_of": run.as_of,
                "status": run.status,
                "created_at": run.created_at.isoformat(),
                "conversation_id": str(run.conversation_id) if run.conversation_id else None,
            }
            for run in runs
        ],
        "next_before": runs[-1].created_at.isoformat() if len(runs) == limit else None,
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, conn: Db, caller: Caller, settings: Settings) -> dict[str, Any]:
    return _run_view(run_job(run_id, conn, caller), settings.trace_ui_url)


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


# Result export (docs/design/2026-11-14-result-export.md): a job per export, indexes not SQL.

EXPIRED_EXPORT = "the export expired; download it again"


@router.post("/runs/{run_id}/queries/{index}/export", status_code=202)
def create_export(
    run_id: str, index: int, conn: Db, caller: Caller, settings: Settings
) -> dict[str, Any]:
    """Write the whole result of one query behind an answer as CSV. The query is named by its
    index in the run's result; its SQL is read from the stored result, never from the request."""
    job = run_job(run_id, conn, caller)
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(status_code=409, detail=f"the run is {job.status}")
    queries = job.result.get("queries") or []
    if not 0 <= index < len(queries):
        raise _not_found()
    dataset_id = _uuid(str(job.payload.get("dataset_id", "")))
    if dataset_changed_since_run(conn, caller.workspace_id, job.id, dataset_id):
        raise HTTPException(status_code=409, detail=CHANGED_DATASET)
    # An export runs a stored query in the sandbox and calls no model.
    _may_start_job(conn, caller, settings, calls_model=False)
    payload = {"run_id": str(job.id), "query_index": index, "dataset_id": str(dataset_id)}
    with conn.transaction():
        export_id = JobQueue(conn).enqueue(caller.workspace_id, "export", payload)
        log(conn, caller, "export.create", str(export_id), run_id=str(job.id), query_index=index)
    return {"id": str(export_id), "status": "queued"}


def export_job(export_id: str, conn: psycopg.Connection, caller: Member) -> Job:
    job = JobQueue(conn).get(_uuid(export_id), caller.workspace_id)
    if job is None or job.kind != "export":
        raise _not_found()
    return job


@router.get("/exports/{export_id}")
def get_export(export_id: str, conn: Db, caller: Caller) -> dict[str, Any]:
    job = export_job(export_id, conn, caller)
    result = job.result or {}
    return {
        "id": str(job.id),
        "status": job.status,
        "rows": result.get("rows"),
        "bytes": result.get("bytes"),
        "filename": result.get("filename"),
        "error": job.last_error if job.status == "failed" else None,
    }


@router.get("/exports/{export_id}/file")
def download_export(
    export_id: str, conn: Db, caller: Caller, settings: Settings
) -> StreamingResponse:
    job = export_job(export_id, conn, caller)
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(status_code=409, detail=f"the export is {job.status}")
    path = export_file(settings.exports_dir, job.id)
    if is_expired(path, now=time.time()):
        raise HTTPException(status_code=404, detail=EXPIRED_EXPORT)
    # Open before answering: the worker may purge the file at any moment, and an open file
    # still reads in full after it is deleted.
    try:
        file = path.open("rb")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=EXPIRED_EXPORT) from None
    log(conn, caller, "export.download", str(job.id))
    filename = str(job.result.get("filename") or f"{job.id}.csv")
    return StreamingResponse(
        _read_and_close(file),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(os.fstat(file.fileno()).st_size),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _read_and_close(file: BinaryIO, chunk_bytes: int = 1 << 20) -> Iterator[bytes]:
    with file:
        while chunk := file.read(chunk_bytes):
            yield chunk


FINISHED = ("succeeded", "failed")


def _sse(event: RunEvent) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.data)}\n\n"


def _event_stream(
    settings: ApiSettings, job_id: UUID, workspace_id: UUID, after_id: int
) -> Iterator[str]:
    """Events after after_id, as they arrive, then an `end` event once the run is finished.

    Its own connection: the request's connection closes when the handler returns."""
    started = last_sent = time.monotonic()
    with connect(settings.database_url, schema=settings.schema) as conn:
        events, queue = RunEvents(conn), JobQueue(conn)
        while True:
            batch = events.after(job_id, workspace_id, after_id=after_id)
            for event in batch:
                yield _sse(event)
                after_id = event.id
            if batch:
                last_sent = time.monotonic()
                continue
            job = queue.get(job_id, workspace_id)
            if job is None or job.status in FINISHED:
                # Events written just before the job finished may have arrived since the last read.
                for event in events.after(job_id, workspace_id, after_id=after_id, limit=10_000):
                    yield _sse(event)
                status = job.status if job else "missing"
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                return
            now = time.monotonic()
            if now - started > settings.events_max_seconds:
                return
            if now - last_sent > settings.events_keepalive_seconds:
                yield ": keep-alive\n\n"
                last_sent = now
            time.sleep(settings.events_poll_seconds)


@router.get("/runs/{run_id}/events")
def stream_run_events(
    run_id: str, request: Request, conn: Db, caller: Caller, settings: Settings
) -> StreamingResponse:
    """Server-sent events: plan, turn, tool, verify, done, and question, then `end` with the run's
    status. Reconnect with the Last-Event-ID header to continue where the stream stopped."""
    job = run_job(run_id, conn, caller)
    last = request.headers.get("last-event-id", "0")
    after_id = int(last) if last.isdigit() else 0
    return StreamingResponse(
        _event_stream(settings, job.id, caller.workspace_id, after_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Operator dashboard (ADR 0026)

NO_STORE = {"Cache-Control": "no-store"}


@router.get("/ops/summary")
def ops_summary(
    conn: Db,
    caller: Operator,
    settings: Settings,
    window: Literal["24h", "7d", "30d"] = "24h",
) -> JSONResponse:
    data = ops.summary(conn, window)
    for row in data["cost"]["today"]:
        if row["budget_usd"] is None:
            row["budget_usd"] = settings.daily_budget_usd
    data["daily_budget_default_usd"] = settings.daily_budget_usd
    return JSONResponse(data, headers=NO_STORE)


@router.get("/ops/failures")
def ops_failures(
    conn: Db,
    caller: Operator,
    settings: Settings,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: dt.datetime | None = None,
) -> JSONResponse:
    rows = ops.failures(conn, limit=limit, before=before.isoformat() if before else None)
    for row in rows:
        row["traces"] = _trace_links(row.pop("trace_ids"), settings.trace_ui_url)
    return JSONResponse({"failures": rows}, headers=NO_STORE)
