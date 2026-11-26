"""Run jobs: answer one question about one dataset with the agent graph.

The job id is the graph's thread id, so a job claimed again after a killed worker or a provider
failure continues from its last checkpoint instead of starting over. A provider failure is
retryable; the run keeps its checkpoint and the retry continues it. Progress events go to
run_events as the graph runs.
"""

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from opentelemetry import trace

from da_agent.agent.answer import result_to_json
from da_agent.agent.checkpoints import checkpoint_connection, checkpoint_version
from da_agent.agent.graph import run_graph
from da_agent.agent.loop_types import AgentLimits, Document
from da_agent.agent.stop import CANCELLED, LEASE_LOST, StopToken
from da_agent.agent.tools import Dataset
from da_agent.llm.client import LLMClient
from da_agent.storage.dataset_versions import payload_version
from da_agent.storage.db import connect
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.versions import mark_resumed_from
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.worker import RetryableJobError

# Returns the dataset at a version and its documents, only if the dataset belongs to the workspace.
DatasetResolver = Callable[[UUID, str, int], tuple[Dataset, list[Document]]]
# Returns the run's memory as documents: the notes in its snapshot and its earlier turns. The
# second argument maps each table to its visible columns, so stale metrics are left out.
MemoryResolver = Callable[[Job, dict[str, set[str]]], list[Document]]


def graph_options(payload: dict[str, Any]) -> dict[str, Any]:
    """The agent options a run job asks for. A job queued before analysis existed (ADR 0051) has
    no analysis key and runs as it was queued, so a resumed run keeps its tools."""
    return {
        "charts": bool(payload.get("charts", True)),
        "analysis": bool(payload.get("analysis", False)),
        "analysis_kind": payload.get("analysis_kind"),
    }


class RunHandler:
    def __init__(
        self,
        *,
        llm: LLMClient,
        datasets: DatasetResolver,
        checkpoint_url: str,
        schema: str | None = None,
        limits: AgentLimits | None = None,
        budgets: WorkspaceBudgets | None = None,
        memory: MemoryResolver | None = None,
        agent_version: str | None = None,
    ) -> None:
        self.llm = llm
        self.datasets = datasets
        self.checkpoint_url = checkpoint_url
        self.schema = schema
        self.limits = limits or AgentLimits()
        # When set, each run's money limit is what its workspace has left today (ADR 0022).
        self.budgets = budgets
        # Memory goes after the dataset's documents (ADR 0030); without it, runs have none.
        self.memory = memory
        # Stamped on every checkpoint, so a resume by another version is marked (ADR 0038).
        self.agent_version = agent_version
        # How often a run asks the queue whether its user cancelled it (ADR 0042).
        self.cancel_poll_seconds = 1.0

    def _prepare(
        self, job: Job, run_id: str | None = None
    ) -> tuple[AgentLimits, Dataset, list[Document]]:
        """The limits, dataset, and documents a run of this job uses. run_id names a run started
        from the job (a fork or a golden rerun), whose budget does not include the job's."""
        limits = self.limits
        if self.budgets is not None:
            limits = replace(limits, budget=self.budgets.for_job(job, run_id))
        dataset, documents = self.datasets(
            job.workspace_id, job.payload["dataset_id"], payload_version(job.payload)
        )
        if self.memory is not None:
            visible = {t.name: {c.name for c in t.columns} for t in dataset.describe()}
            documents = [*documents, *self.memory(job, visible)]
        return limits, dataset, documents

    def fork(self, job: Job, fork_id: str) -> dict[str, Any]:
        """Run on in thread fork_id, which time travel started from one of the job's checkpoints
        (ADR 0042). Same dataset, documents, and limits as the job; costs go under fork_id. The
        job and its own thread are not written."""
        limits, dataset, documents = self._prepare(job, fork_id)
        with checkpoint_connection(self.checkpoint_url, self.schema) as conn:
            result = run_graph(
                job.payload["question"],
                as_of=job.payload["as_of"],
                dataset=dataset,
                documents=documents,
                llm=self.llm,
                run_id=fork_id,
                thread_id=fork_id,
                limits=limits,
                checkpointer=PostgresSaver(conn),
                **graph_options(job.payload),
                agent_version=self.agent_version,
            )
        return result_to_json(result)

    def rerun(self, job: Job, run_id: str) -> dict[str, Any]:
        """Answer the job's question again with this handler's code, in this process and with no
        checkpoint (ADR 0043). Same dataset, documents, memory, and limits as the job; costs go
        under run_id. No job is created, so the rerun stays out of the user's history."""
        limits, dataset, documents = self._prepare(job, run_id)
        result = run_graph(
            job.payload["question"],
            as_of=job.payload["as_of"],
            dataset=dataset,
            documents=documents,
            llm=self.llm,
            run_id=run_id,
            limits=limits,
            **graph_options(job.payload),
            agent_version=self.agent_version,
        )
        return result_to_json(result)

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        payload = job.payload
        limits, dataset, documents = self._prepare(job)
        with (
            checkpoint_connection(self.checkpoint_url, self.schema) as conn,
            connect(self.checkpoint_url, schema=self.schema) as events_conn,
        ):
            events = RunEvents(events_conn)
            queue = JobQueue(events_conn)
            # The stop probe reads through this connection on the run's thread: a stalled
            # database must not stall the run with it.
            events_conn.execute("SET statement_timeout = '5s'")

            def stop_asked() -> str | None:
                # A lost lease means another worker owns the job now: stop spending at once.
                if lost.is_set():
                    return LEASE_LOST
                return CANCELLED if queue.cancel_requested(job.id) else None

            stop = StopToken(
                limits.max_seconds, probe=stop_asked, probe_every=self.cancel_poll_seconds
            )
            saver = PostgresSaver(conn)
            saver.setup()
            self._mark_resume(saver, job, events_conn)
            result = run_graph(
                payload["question"],
                as_of=payload["as_of"],
                dataset=dataset,
                documents=documents,
                llm=self.llm,
                run_id=str(job.id),
                limits=limits,
                checkpointer=saver,
                **graph_options(payload),
                on_event=lambda event: events.append(job.id, job.workspace_id, event),
                agent_version=self.agent_version,
                stop=stop,
            )
        if result.stop_reason == "llm_error":
            raise RetryableJobError("the model provider failed; the run will continue later")
        return result_to_json(result)

    def _mark_resume(self, saver: PostgresSaver, job: Job, conn: psycopg.Connection[Any]) -> None:
        """Mark this attempt when it continues a checkpoint that another agent version wrote.

        A checkpoint from before versions names none, which is unknown rather than different."""
        found, previous = checkpoint_version(saver, str(job.id))
        if not found or previous is None or previous == self.agent_version:
            return
        span = trace.get_current_span()
        span.set_attribute("da_agent.resumed_from_version", previous)
        try:
            mark_resumed_from(conn, job.id, job.attempts, previous)
        except psycopg.Error as error:
            # A record of the resume; never a reason to fail the run.
            span.record_exception(error)
