"""The worker loop: claim a job, keep its lease alive, run its handler, and record the outcome.

A handler gets the job and an event that is set if the worker loses the job (its lease ran out and
another worker took it). The outcome is written only while this worker still owns the job.

- A handler that returns a dict completes the job with it.
- RetryableJobError queues the job again after the next delay in retry_delays, and
  PermanentJobError fails it with its message.
- JobWaitError puts the job back for later and gives its attempt back, so waiting never uses up
  max_attempts.
- Any other exception fails the job. Only the exception type is stored, because the job row is
  shown to the workspace and a message can hold host paths; the trace keeps the full exception.
- A BaseException that is not an Exception, such as the process being stopped, is not caught: the
  job keeps its lease, and another worker claims it once the lease runs out.

See docs/adr/0017 and docs/adr/0018.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from opentelemetry import trace

from da_agent.agent.stop import CANCELLED
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.versions import record_attempt
from da_agent.telemetry.metrics import WorkerMetrics, run_outcome

logger = logging.getLogger(__name__)

Handler = Callable[[Job, threading.Event], dict[str, Any]]
MAX_ERROR_CHARS = 500


class RetryableJobError(Exception):
    """A failure that may pass on a later attempt. The message is shown to the workspace."""


class JobWaitError(Exception):
    """The job cannot go on yet, and no retry is spent: it is queued again after `seconds`."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"wait {seconds} s")
        self.seconds = seconds


class PermanentJobError(Exception):
    """A failure that no retry can fix, such as a missing dataset. The message is shown to the
    workspace."""


@dataclass(frozen=True)
class WorkerConfig:
    url: str
    worker_id: str = ""
    # For tests: run every query in one Postgres schema.
    schema: str | None = None
    lease_seconds: float = 60.0
    heartbeat_seconds: float = 15.0
    poll_seconds: float = 1.0
    # Delay before each retry; the last one repeats.
    retry_delays: tuple[float, ...] = (10.0, 60.0, 300.0)
    # The registered agent version this worker runs (ADR 0038); recorded on every attempt.
    agent_version: str | None = None


def _failure_ending(status: str | None) -> dict[str, Any]:
    """How a failed attempt ended, from the status the queue gave the job: queued again (a
    retry), failed or cancelled for good, or None when another worker holds it now."""
    if status == "queued":
        return {"attempt": "retry", "status": None, "result": None}
    if status is None:
        return {"attempt": "lost", "status": None, "result": None}
    attempt = "cancelled" if status == "cancelled" else "failed"
    return {"attempt": attempt, "status": status, "result": None}


class Worker:
    def __init__(
        self,
        config: WorkerConfig,
        handlers: dict[str, Handler],
        *,
        tracer: trace.Tracer | None = None,
        metrics: WorkerMetrics | None = None,
    ) -> None:
        self.config = config
        self.worker_id = config.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.handlers = handlers
        self._tracer = tracer or trace.get_tracer("da_agent.worker")
        # Counters behind the SLOs and alerts (ADR 0044). None records nothing.
        self._metrics = metrics

    def _connect(self):
        return connect(self.config.url, schema=self.config.schema)

    def run_once(self) -> bool:
        """Claim and process one job. Returns False when no job was due."""
        with self._connect() as conn:
            queue = JobQueue(conn)
            for kind, status in queue.fail_expired():
                # Their worker died mid-attempt: count the ending it never could.
                self._record_ending(kind, "lost", status, None)
            job = queue.claim(self.worker_id, lease_seconds=self.config.lease_seconds)
            if job is None:
                return False
            with self._tracer.start_as_current_span(f"job.{job.kind}") as span:
                span.set_attribute("da_agent.job_id", str(job.id))
                span.set_attribute("da_agent.workspace_id", str(job.workspace_id))
                span.set_attribute("da_agent.job_attempt", job.attempts)
                version = self.config.agent_version
                if version is not None:
                    span.set_attribute("da_agent.agent_version", version)
                    span.set_attribute("gen_ai.agent.version", version)
                context = span.get_span_context()
                trace_id = None
                if context.is_valid:
                    # Lets an operator open each attempt's trace from the run (ADR 0023).
                    trace_id = trace.format_trace_id(context.trace_id)
                    queue.add_trace(job.id, self.worker_id, trace_id)
                try:
                    record_attempt(conn, job.id, job.attempts, self.worker_id, version, trace_id)
                except psycopg.Error as error:
                    # A record of which agent ran the job; never a reason to fail the job.
                    span.record_exception(error)
                self._process(queue, job)
            return True

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(self.config.poll_seconds)

    def _process(self, queue: JobQueue, job: Job) -> None:
        lost = threading.Event()
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, lost, done), daemon=True)
        beat.start()
        started = time.monotonic()
        # How the attempt ended, and the job's status and result when it ended for good.
        ending: dict[str, Any] = {"attempt": "failed", "status": "failed", "result": None}
        if self._metrics is not None and job.attempts == 1 and job.queue_wait_seconds is not None:
            self._metrics.queue_wait(job.queue_wait_seconds)
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                status = queue.fail(job.id, self.worker_id, f"no handler for {job.kind!r} jobs",
                                    retry_in_seconds=None)  # fmt: skip
                ending = _failure_ending(status)
                return
            try:
                result = handler(job, lost)
            except PermanentJobError as error:
                message = str(error)[:MAX_ERROR_CHARS]
                status = queue.fail(job.id, self.worker_id, message, retry_in_seconds=None)
                ending = _failure_ending(status)
                return
            except JobWaitError as wait:
                held = queue.wait(job.id, self.worker_id, seconds=wait.seconds)
                ending = {"attempt": "wait" if held else "lost", "status": None, "result": None}
                return
            except RetryableJobError as error:
                delays = self.config.retry_delays
                delay = delays[min(job.attempts, len(delays)) - 1] if delays else 0.0
                message = str(error)[:MAX_ERROR_CHARS]
                status = queue.fail(job.id, self.worker_id, message, retry_in_seconds=delay)
                ending = _failure_ending(status)
                return
            except Exception as error:  # Any crash fails the job, by design.
                trace.get_current_span().record_exception(error)
                message = f"the job failed unexpectedly ({type(error).__name__})"
                status = queue.fail(job.id, self.worker_id, message, retry_in_seconds=None)
                ending = _failure_ending(status)
                return
            if lost.is_set():
                # Another worker may own the job now; its result is the one that counts.
                trace.get_current_span().set_attribute("da_agent.job_lost", True)
                ending = {"attempt": "lost", "status": None, "result": None}
                return
            if result.get("stop_reason") == CANCELLED:
                finished = queue.finish_cancelled(job.id, self.worker_id, result)
                ending = {"attempt": "cancelled", "status": "cancelled", "result": result}
            else:
                finished = queue.complete(job.id, self.worker_id, result)
                ending = {"attempt": "succeeded", "status": "succeeded", "result": result}
            if not finished:
                trace.get_current_span().set_attribute("da_agent.job_lost", True)
                ending = {"attempt": "lost", "status": None, "result": None}
        finally:
            done.set()
            beat.join()
            self._record(job, ending, time.monotonic() - started)

    def _record(self, job: Job, ending: dict[str, Any], seconds: float) -> None:
        if self._metrics is None:
            return
        if job.kind == "run":
            self._metrics.run_duration(seconds)
        self._record_ending(job.kind, ending["attempt"], ending["status"], ending["result"])

    def _record_ending(
        self, kind: str, attempt: str, status: str | None, result: dict[str, Any] | None
    ) -> None:
        """Count one attempt, and the run when it ended for good. Never raises: a metric must not
        stop the worker (ADR 0044)."""
        if self._metrics is None:
            return
        try:
            self._metrics.attempt(kind, attempt)
            if kind == "run" and status is not None:
                self._metrics.run_finished(run_outcome(status, result))
        except Exception:
            logger.exception("could not record a job's metrics")

    def _heartbeat(self, job: Job, lost: threading.Event, done: threading.Event) -> None:
        # Its own connection: a psycopg connection must not be shared between threads at once.
        with self._connect() as conn:
            queue = JobQueue(conn)
            while not done.wait(self.config.heartbeat_seconds):
                if not queue.heartbeat(
                    job.id, self.worker_id, lease_seconds=self.config.lease_seconds
                ):
                    lost.set()
                    return
