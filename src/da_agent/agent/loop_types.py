"""Inputs of the agent loop."""

from dataclasses import dataclass

from da_agent.llm.client import RunBudget


@dataclass(frozen=True)
class AgentLimits:
    max_iterations: int = 10
    max_nudges: int = 2
    # Verification failures sent back to the tool loop before the answer is withheld.
    max_repairs: int = 2
    budget: RunBudget | None = None


@dataclass(frozen=True)
class Document:
    """A document for the model. An on-demand one shows only its section titles in the prompt,
    and the model fetches sections with search_rules."""

    name: str
    text: str
    on_demand: bool = False
