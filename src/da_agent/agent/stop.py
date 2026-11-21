"""Cooperative stops for a run: a user cancel, a deadline, or a lost lease (ADR 0042).

Python cannot kill a thread, so a run stops itself: the graph and the loop agent check the token
before every model call and between nodes, and end with the token's reason as the stop reason.
A model call or sandbox query already running finishes within its own timeout first.

A probe lets the token ask an outside source, such as the job queue, whether the run should stop.
It is asked at most once per probe_every seconds, on the run's own thread, and its failures are
ignored: a broken probe must not stop a run.
"""

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEADLINE_EXCEEDED = "deadline_exceeded"
CANCELLED = "cancelled"
# The worker's lease ran out and another worker may own the job: stop, and do not complete it.
LEASE_LOST = "lease_lost"


class RunStopped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"the run stopped: {reason}")
        self.reason = reason


class StopToken:
    """Thread-safe: a worker's watcher thread cancels it while the run thread checks it."""

    def __init__(
        self,
        deadline_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        probe: Callable[[], str | None] | None = None,
        probe_every: float = 1.0,
    ) -> None:
        self._clock = clock
        self._deadline = clock() + deadline_seconds if deadline_seconds is not None else None
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._probe = probe
        self._probe_every = probe_every
        self._probed_at = float("-inf")
        self._probe_failed = False

    def cancel(self, reason: str) -> None:
        """Ask the run to stop. The first reason given wins."""
        with self._lock:
            if self._reason is None:
                self._reason = reason

    @property
    def reason(self) -> str | None:
        with self._lock:
            now = self._clock()
            expired = self._deadline is not None and now >= self._deadline
            if self._reason is None and expired:
                self._reason = DEADLINE_EXCEEDED
            if (
                self._reason is None
                and self._probe is not None
                and now - self._probed_at >= self._probe_every
            ):
                self._probed_at = now
                try:
                    self._reason = self._probe()
                    self._probe_failed = False
                except Exception:
                    # Once per outage, not once a second: the run goes on either way.
                    if not self._probe_failed:
                        logger.exception("the stop probe failed; the run goes on")
                    self._probe_failed = True
            return self._reason

    def check(self) -> None:
        reason = self.reason
        if reason is not None:
            raise RunStopped(reason)
