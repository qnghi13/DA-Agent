"""Aggregates for the operator dashboard (ADR 0026), read from the live tables.

Everything here is counts, timings, money, ids, and names. Questions, answers, verify problems,
and SQL errors can hold users' data, so they never leave their workspace through these queries;
the operator opens the run's trace for detail. Buckets are UTC hours (24h) or days (7d, 30d).
"""

# The SQL below is assembled only from this module's constants; every value is a parameter.
# ruff: noqa: S608

import datetime as dt
from typing import Any

import psycopg
from psycopg.rows import dict_row

WINDOWS = {
    "24h": (dt.timedelta(hours=24), "hour"),
    "7d": (dt.timedelta(days=7), "day"),
    "30d": (dt.timedelta(days=30), "day"),
}
MAX_ERROR_CHARS = 300

# One outcome per run job. "answered" with an "unanswerable" answer is an abstention. The
# placeholder {j} is the jobs table's alias, with its dot, or nothing.
_OUTCOME = """
CASE
    WHEN {j}status IN ('queued', 'running') THEN 'in_progress'
    WHEN {j}status = 'failed' THEN 'failed'
    WHEN {j}result ->> 'stop_reason' = 'answered'
        AND {j}result -> 'answer' ->> 'kind' = 'unanswerable' THEN 'abstained'
    WHEN {j}result ->> 'stop_reason' = 'answered' THEN 'answered'
    WHEN {j}result ->> 'stop_reason' IN ('unverified', 'unreadable_values') THEN 'unverified'
    WHEN {j}result ->> 'stop_reason' IN ('llm_error', 'invalid_tool_call', 'no_tool_call')
        THEN 'model_error'
    WHEN {j}result ->> 'stop_reason' IN ('budget_exceeded', 'iteration_limit',
        'needs_clarification') THEN {j}result ->> 'stop_reason'
    ELSE 'other'
END
"""
OUTCOME = _OUTCOME.format(j="")
JOB_OUTCOME = _OUTCOME.format(j="j.")


def _iso(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z") if value else None


def _rows(conn: psycopg.Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(sql, params).fetchall()  # type: ignore[arg-type]


def _one(conn: psycopg.Connection, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    rows = _rows(conn, sql, params)
    return rows[0] if rows else {}


def _bucketed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "bucket": _iso(row["bucket"])} for row in rows]


def _now_panel(conn: psycopg.Connection) -> dict[str, Any]:
    row = _one(
        conn,
        """
        SELECT
            count(*) FILTER (WHERE status = 'queued') AS queued,
            count(*) FILTER (WHERE status = 'running') AS running,
            extract(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued'))
                AS oldest_queued_seconds,
            count(*) FILTER (WHERE status = 'running' AND lease_until < now()) AS expired_leases,
            max(updated_at) FILTER (WHERE status <> 'queued' OR attempts > 0)
                AS last_worker_activity
        FROM jobs
        """,
        {},
    )
    datasets = _one(
        conn,
        "SELECT count(*) FILTER (WHERE status = 'proposed') AS awaiting_review, "
        "count(*) FILTER (WHERE status IN ('uploaded', 'building')) AS processing FROM datasets",
        {},
    )
    seconds = row["oldest_queued_seconds"]
    return {
        "queued": row["queued"],
        "running": row["running"],
        "oldest_queued_seconds": float(seconds) if seconds is not None else None,
        "expired_leases": row["expired_leases"],
        "last_worker_activity": _iso(row["last_worker_activity"]),
        "datasets_awaiting_review": datasets["awaiting_review"],
        "datasets_processing": datasets["processing"],
    }


def _runs_panel(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    in_window = "kind = 'run' AND created_at >= now() - %(span)s"
    counts = _rows(
        conn,
        f"SELECT {OUTCOME} AS outcome, count(*) AS n FROM jobs WHERE {in_window} GROUP BY 1",
        params,
    )
    outcomes = {row["outcome"]: row["n"] for row in counts}
    total = sum(outcomes.values())
    finished = total - outcomes.get("in_progress", 0)
    series = _rows(
        conn,
        f"SELECT date_trunc(%(bucket)s, created_at, 'UTC') AS bucket, {OUTCOME} AS outcome, "
        f"count(*) AS count FROM jobs WHERE {in_window} GROUP BY 1, 2 ORDER BY 1, 2",
        params,
    )
    retried = _one(
        conn,
        f"SELECT count(*) AS n FROM jobs WHERE {in_window} AND attempts > 1",
        params,
    )["n"]
    return {
        "total": total,
        "outcomes": outcomes,
        "answer_rate": outcomes.get("answered", 0) / finished if finished else None,
        "abstain_rate": outcomes.get("abstained", 0) / finished if finished else None,
        "retried": retried,
        "series": _bucketed(series),
    }


def _latency_panel(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    wait = "extract(epoch FROM started_at - created_at)"
    took = "extract(epoch FROM finished_at - started_at)"
    where = "kind = 'run' AND created_at >= now() - %(span)s"
    row = _one(
        conn,
        f"""
        SELECT
            percentile_cont(0.5) WITHIN GROUP (ORDER BY {wait}) AS queue_wait_p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY {wait}) AS queue_wait_p95,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY {took}) AS run_p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY {took}) AS run_p95
        FROM jobs WHERE {where} AND started_at IS NOT NULL
        """,
        params,
    )
    series = _rows(
        conn,
        f"""
        SELECT date_trunc(%(bucket)s, created_at, 'UTC') AS bucket,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY {took}) AS run_p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY {took}) AS run_p95
        FROM jobs WHERE {where} AND finished_at IS NOT NULL AND started_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )
    return {**row, "series": _bucketed(series)}


def _model_panels(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    in_window = "created_at >= now() - %(span)s"
    models = _rows(
        conn,
        f"""
        SELECT provider, model,
            count(*) FILTER (WHERE error_kind IS NULL) AS calls,
            count(*) FILTER (WHERE error_kind IS NOT NULL) AS errors,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_seconds)
                FILTER (WHERE error_kind IS NULL) AS latency_p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_seconds)
                FILTER (WHERE error_kind IS NULL) AS latency_p95,
            sum(input_tokens) AS input_tokens,
            sum(output_tokens) AS output_tokens,
            sum(cached_input_tokens)::float8 / nullif(sum(input_tokens), 0) AS cached_share,
            sum(cost_usd) AS cost_usd
        FROM llm_calls WHERE {in_window}
        GROUP BY 1, 2 ORDER BY cost_usd DESC, calls DESC
        """,
        params,
    )
    kinds = _rows(
        conn,
        f"SELECT error_kind AS kind, count(*) AS n FROM llm_calls "
        f"WHERE {in_window} AND error_kind IS NOT NULL GROUP BY 1",
        params,
    )
    series = _rows(
        conn,
        f"SELECT date_trunc(%(bucket)s, created_at, 'UTC') AS bucket, error_kind AS kind, "
        f"count(*) AS count FROM llm_calls WHERE {in_window} AND error_kind IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 1, 2",
        params,
    )
    by_kind = {row["kind"]: row["n"] for row in kinds}
    return {
        "models": models,
        "model_errors": {
            "total": sum(by_kind.values()),
            "by_kind": by_kind,
            "series": _bucketed(series),
        },
    }


def _cost_panel(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    in_window = "c.created_at >= now() - %(span)s"
    total = _one(
        conn,
        f"SELECT coalesce(sum(cost_usd), 0) AS usd FROM llm_calls c WHERE {in_window}",
        params,
    )["usd"]
    answered = _one(
        conn,
        f"""
        SELECT count(DISTINCT j.id) AS runs, coalesce(sum(c.cost_usd), 0) AS usd
        FROM jobs j LEFT JOIN llm_calls c ON c.run_id = j.id::text
        WHERE j.kind = 'run' AND j.created_at >= now() - %(span)s AND {JOB_OUTCOME} = 'answered'
        """,
        params,
    )
    series = _rows(
        conn,
        f"SELECT date_trunc(%(bucket)s, c.created_at, 'UTC') AS bucket, sum(cost_usd) AS cost_usd "
        f"FROM llm_calls c WHERE {in_window} GROUP BY 1 ORDER BY 1",
        params,
    )
    by_workspace = _rows(
        conn,
        f"""
        SELECT w.id::text AS workspace_id, w.name, sum(c.cost_usd) AS cost_usd,
            count(DISTINCT c.run_id) AS runs
        FROM llm_calls c JOIN workspaces w ON w.id = c.workspace_id
        WHERE {in_window} GROUP BY 1, 2 ORDER BY cost_usd DESC LIMIT 10
        """,
        params,
    )
    today = _rows(
        conn,
        """
        SELECT w.id::text AS workspace_id, w.name, sum(c.cost_usd) AS spent_usd,
            w.daily_budget_usd AS budget_usd
        FROM llm_calls c JOIN workspaces w ON w.id = c.workspace_id
        WHERE c.created_at >= date_trunc('day', now(), 'UTC')
        GROUP BY 1, 2, 4 ORDER BY spent_usd DESC LIMIT 20
        """,
        params,
    )
    return {
        "total_usd": total,
        "per_answered_run_usd": answered["usd"] / answered["runs"] if answered["runs"] else None,
        "series": _bucketed(series),
        "by_workspace": by_workspace,
        "today": today,
    }


def _quality_panel(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    events = _one(
        conn,
        """
        SELECT
            count(*) FILTER (WHERE type = 'verify') AS verify_checks,
            count(*) FILTER (WHERE type = 'verify' AND data ->> 'passed' = 'false')
                AS verify_failed,
            count(DISTINCT job_id) FILTER (WHERE type = 'verify' AND data ->> 'passed' = 'false')
                AS runs_repaired,
            count(*) FILTER (WHERE type = 'tool') AS tool_calls,
            count(*) FILTER (WHERE type = 'tool' AND data ->> 'ok' = 'false') AS tool_errors
        FROM run_events WHERE created_at >= now() - %(span)s
        """,
        params,
    )
    ingest = _one(
        conn,
        "SELECT count(*) AS ingest_jobs, count(*) FILTER (WHERE status = 'failed') "
        "AS ingest_failed FROM jobs WHERE kind IN ('ingest', 'build') "
        "AND created_at >= now() - %(span)s",
        params,
    )
    return {**events, **ingest}


def _security_panel(conn: psycopg.Connection, params: dict[str, Any]) -> dict[str, Any]:
    counts = _one(
        conn,
        "SELECT count(*) FILTER (WHERE action = 'auth.failed') AS auth_failed, "
        "count(*) FILTER (WHERE action = 'auth.sign_in') AS sign_ins "
        "FROM audit_log WHERE created_at >= now() - %(span)s",
        params,
    )
    series = _rows(
        conn,
        "SELECT date_trunc(%(bucket)s, created_at, 'UTC') AS bucket, count(*) AS count "
        "FROM audit_log WHERE action = 'auth.failed' AND created_at >= now() - %(span)s "
        "GROUP BY 1 ORDER BY 1",
        params,
    )
    return {**counts, "series": _bucketed(series)}


def summary(conn: psycopg.Connection, window: str) -> dict[str, Any]:
    """Every dashboard panel for the last 24h, 7d, or 30d."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}; use one of {', '.join(WINDOWS)}")
    span, bucket = WINDOWS[window]
    params = {"span": span, "bucket": bucket}
    return {
        "window": window,
        "bucket": bucket,
        "generated_at": _iso(dt.datetime.now(dt.UTC)),
        "now": _now_panel(conn),
        "runs": _runs_panel(conn, params),
        "latency": _latency_panel(conn, params),
        **_model_panels(conn, params),
        "cost": _cost_panel(conn, params),
        "quality": _quality_panel(conn, params),
        "security": _security_panel(conn, params),
    }


def failures(
    conn: psycopg.Connection, *, limit: int, before: str | None = None
) -> list[dict[str, Any]]:
    """Jobs that failed, are waiting to retry, or runs that stopped without an answer.

    Newest first. The error is the worker's own message, cut to 300 characters; a run's stop
    detail is left out, since it quotes the data.
    """
    rows = _rows(
        conn,
        """
        SELECT j.id::text AS id, j.kind, j.workspace_id::text AS workspace_id,
            w.name AS workspace_name, j.status, j.attempts,
            j.result ->> 'stop_reason' AS stop_reason,
            left(j.last_error, %(cut)s) AS error, j.created_at, j.finished_at, j.trace_ids
        FROM jobs j JOIN workspaces w ON w.id = j.workspace_id
        WHERE (j.status = 'failed'
            OR (j.status = 'queued' AND j.last_error IS NOT NULL)
            OR (j.status = 'succeeded' AND j.kind = 'run'
                AND j.result ->> 'stop_reason' NOT IN ('answered', 'needs_clarification')))
          AND (%(before)s::timestamptz IS NULL OR j.created_at < %(before)s::timestamptz)
        ORDER BY j.created_at DESC, j.id DESC
        LIMIT %(limit)s
        """,
        {"cut": MAX_ERROR_CHARS, "before": before, "limit": limit},
    )
    return [
        {**row, "created_at": _iso(row["created_at"]), "finished_at": _iso(row["finished_at"])}
        for row in rows
    ]
