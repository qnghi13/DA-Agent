"""Money limits for jobs that call a model. A job may spend up to the per-run limit, and never
more than its workspace has left for the day. See docs/adr/0022."""

from dataclasses import dataclass

from da_agent.llm.client import RunBudget
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job
from da_agent.storage.ledger import PostgresCostLedger, daily_budget, spent_today
from da_agent.worker.worker import PermanentJobError


@dataclass(frozen=True)
class WorkspaceBudgets:
    url: str
    daily_usd: float
    run_usd: float
    run_tokens: int | None = None
    schema: str | None = None

    def for_job(self, job: Job) -> RunBudget:
        """The budget for one job, or PermanentJobError if the workspace has nothing left today.

        The run's own earlier spending, from attempts before a retry, counts toward its limit."""
        with connect(self.url, schema=self.schema) as conn:
            limit = daily_budget(conn, job.workspace_id, self.daily_usd)
            left = limit - spent_today(conn, job.workspace_id)
            if left <= 0:
                raise PermanentJobError(
                    f"the workspace has reached its daily model budget (${limit:.2f}); "
                    "try again tomorrow"
                )
            spent = PostgresCostLedger(conn).totals(str(job.id)).cost_usd
        return RunBudget(
            max_total_tokens=self.run_tokens, max_cost_usd=min(self.run_usd, spent + left)
        )
