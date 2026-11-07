"""The worker loop: claim a job, keep its lease alive, run its handler, and record the outcome.

A handler gets the job and an event that is set if the worker loses the job (its lease ran out and
another worker took it). The outcome is written only while this worker still owns the job.

- A handler that returns a dict completes the job with it.
- RetryableJobError queues the job again after the next delay in retry_delays, and
  PermanentJobError fails it with its message.
- Any other exception fails the job. Only the exception type is stored, because the job row is
  shown to the workspace and a message can hold host paths; the trace keeps the full exception.
- A BaseException that is not an Exception, such as the process being stopped, is not caught: the
  job keeps its lease, and another worker claims it once the lease runs out.

See docs/adr/0017 and docs/adr/0018.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace

from da_agent.storage.db import connect
from da_agent.storage.jobs import Job, JobQueue

Handler = Callable[[Job, threading.Event], dict[str, Any]]
MAX_ERROR_CHARS = 500


class RetryableJobError(Exception):
    """A failure that may pass on a later attempt. The message is shown to the workspace."""


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


class Worker:
    def __init__(self, config: WorkerConfig, handlers: dict[str, Handler]) -> None:
        self.config = config
        self.worker_id = config.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.handlers = handlers
        self._tracer = trace.get_tracer("da_agent.worker")

    def _connect(self):
        return connect(self.config.url, schema=self.config.schema)

    def run_once(self) -> bool:
        """Claim and process one job. Returns False when no job was due."""
        with self._connect() as conn:
            queue = JobQueue(conn)
            queue.fail_expired()
            job = queue.claim(self.worker_id, lease_seconds=self.config.lease_seconds)
            if job is None:
                return False
            with self._tracer.start_as_current_span(f"job.{job.kind}") as span:
                span.set_attribute("da_agent.job_id", str(job.id))
                span.set_attribute("da_agent.workspace_id", str(job.workspace_id))
                span.set_attribute("da_agent.job_attempt", job.attempts)
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
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                queue.fail(job.id, self.worker_id, f"no handler for {job.kind!r} jobs",
                           retry_in_seconds=None)  # fmt: skip
                return
            try:
                result = handler(job, lost)
            except PermanentJobError as error:
                message = str(error)[:MAX_ERROR_CHARS]
                queue.fail(job.id, self.worker_id, message, retry_in_seconds=None)
                return
            except RetryableJobError as error:
                delays = self.config.retry_delays
                delay = delays[min(job.attempts, len(delays)) - 1] if delays else 0.0
                message = str(error)[:MAX_ERROR_CHARS]
                queue.fail(job.id, self.worker_id, message, retry_in_seconds=delay)
                return
            except Exception as error:  # Any crash fails the job, by design.
                trace.get_current_span().record_exception(error)
                message = f"the job failed unexpectedly ({type(error).__name__})"
                queue.fail(job.id, self.worker_id, message, retry_in_seconds=None)
                return
            if not queue.complete(job.id, self.worker_id, result):
                trace.get_current_span().set_attribute("da_agent.job_lost", True)
        finally:
            done.set()
            beat.join()

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
