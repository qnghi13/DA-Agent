# 0001: LangGraph for orchestration

**Status:** Accepted, 2026-11-05

**Context:** Runs must pause for user input, survive worker restarts, and follow a graph with bounded
repair loops.

**Decision:** Use LangGraph from the start, with its Postgres checkpointer.

**Consequences:**
- Checkpoints, interrupts, and resume come ready to use.
- The framework hides reducers and checkpoint behaviour, so learning notes must explain them.
- Checkpoints tie state schema changes to stored data.
- Nodes must be safe to resume, because a resume re-enters a node.

**Rejected:**
- Hand-written state machine: teaches more, but means rebuilding durable resume.
- Temporal: strong durability, but one more heavy service on a 7.6 GB machine.
