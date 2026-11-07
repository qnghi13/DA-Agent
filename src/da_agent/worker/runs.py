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
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row

from da_agent.agent.answer import result_to_json
from da_agent.agent.graph import run_graph
from da_agent.agent.loop_types import AgentLimits, Document
from da_agent.agent.tools import Dataset
from da_agent.llm.client import LLMClient
from da_agent.storage.db import connect, search_path_option
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import Job
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.worker import RetryableJobError

# Returns the dataset and its documents, only if the dataset belongs to the workspace.
DatasetResolver = Callable[[UUID, str], tuple[Dataset, list[Document]]]


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
    ) -> None:
        self.llm = llm
        self.datasets = datasets
        self.checkpoint_url = checkpoint_url
        self.schema = schema
        self.limits = limits or AgentLimits()
        # When set, each run's money limit is what its workspace has left today (ADR 0022).
        self.budgets = budgets

    def _checkpoint_connection(self) -> psycopg.Connection[DictRow]:
        conninfo = make_conninfo(self.checkpoint_url, options=search_path_option(self.schema))
        # The settings PostgresSaver.from_conn_string uses.
        return psycopg.Connection[DictRow].connect(
            conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
        )

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        payload = job.payload
        limits = self.limits
        if self.budgets is not None:
            limits = replace(limits, budget=self.budgets.for_job(job))
        dataset, documents = self.datasets(job.workspace_id, payload["dataset_id"])
        with (
            self._checkpoint_connection() as conn,
            connect(self.checkpoint_url, schema=self.schema) as events_conn,
        ):
            events = RunEvents(events_conn)
            saver = PostgresSaver(conn)
            saver.setup()
            result = run_graph(
                payload["question"],
                as_of=payload["as_of"],
                dataset=dataset,
                documents=documents,
                llm=self.llm,
                run_id=str(job.id),
                limits=limits,
                checkpointer=saver,
                charts=bool(payload.get("charts", True)),
                on_event=lambda event: events.append(job.id, job.workspace_id, event),
            )
        if result.stop_reason == "llm_error":
            raise RetryableJobError("the model provider failed; the run will continue later")
        return result_to_json(result)
