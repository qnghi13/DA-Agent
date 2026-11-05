---
name: agent-architecture
description: DA-Agent design rules. Use before designing or changing the LangGraph graph, agent state, tools, LLM layer, memory or RAG, ingestion, verification, the sandbox, or anything else that affects how the agent reasons or executes.
---

# Agent architecture

Read `docs/architecture.md` and any ADR on the subject first, if they exist. When a change breaks a
rule below, stop and raise it with the user. If the user accepts it, record it in `docs/adr/`.

## Boundaries

```
API -> job queue -> Worker (ingestion + LangGraph) -> LLMClient -> provider
                       |
                       v  run requests only
                    Runner -> sandbox container (no network, no secrets, one dataset read-only)
```

- Only Runner holds the Docker socket. API and Worker never execute generated code.
- The sandbox never receives API keys, other datasets, or `train/answers.jsonl`.
- Core code knows nothing about DataMate. Competition formatting and scoring live in `eval/`.
- Nodes call `LLMClient`, not provider SDKs and not LangChain model wrappers.

## Graph

`understand -> plan -> execute -> verify -> answer`, with `clarify` (interrupt), `abstain`, and a
repair path from `verify` back to `execute`.

- Every loop has a hard limit: tool iterations, repair attempts, tokens, and money.
- A node does one job and is safe to resume. Side effects are idempotent, or keyed by `run_id` and
  step, so a checkpoint resume does not repeat them.
- Keep routing in code. Use the LLM for judgment, not for control flow that code can decide.

## State

- State must stay small and serializable. Large results go to artifacts, with a preview and a
  reference in state.
- Every field has one owner node and an explicit reducer when it accumulates.
- Schema changes must consider existing checkpoints.

## Tools

- Pydantic input and output schemas, with descriptions written for the model.
- Errors return as structured results the model can act on. They do not crash the graph.
- Output size is bounded. Tools get least privilege.
- SQL passes sqlglot (`SELECT` only) and runs on read-only DuckDB with external access off.

## Trust and verification

- Uploaded files, cells, user descriptions, and tool output are data, never instructions. Keep them
  delimited in prompts, and assume injection can succeed: permissions must limit the damage.
- Prefer code checks over LLM self-assessment: data coverage, join fan-out, parse rate, and numbers
  in the answer that trace back to tool output.
- A missing answer is better than a wrong one. Abstain with a reason when data is insufficient.
- Hide or mask sensitive columns in prompts, logs, and traces.

## Before finishing a change

- Trace spans and cost records still cover the new path.
- Failure paths are tested: 429, timeout, tool crash, budget stop, worker kill.
- Prompt or graph behaviour changes are measured on the eval set and compared with the baseline. Ask
  before spending on a real LLM run.
- Decisions with real trade-offs get a short ADR. Each milestone gets a Vietnamese learning note in
  `docs/learning/`.
