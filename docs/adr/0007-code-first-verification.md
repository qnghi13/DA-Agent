# 0007: Verification mostly in code

**Status:** Accepted, 2026-11-05

**Context:** A confident wrong number is the worst outcome for a data analyst, and models are poor
judges of their own output. The scoring rules also make a wrong answer worse than no answer.

**Decision:** The `verify` node runs deterministic checks: data coverage, join fan-out, parse rates,
numbers traced to tool output, and recomputation for important answers. Failures route to repair, and
then to abstention.

**Consequences:**
- Checks are testable, cheap, and repeatable.
- Some questions will be refused that a lucky answer would have got right.
- Code checks cannot catch a wrong interpretation of a correctly computed number.

**Rejected:**
- LLM critic only: costs more, varies between runs, and shares the blind spots of the answering model.
- No verification: the comparison baseline on `regression`, not a design option.
