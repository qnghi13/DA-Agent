# 0006: LLM-proposed, user-approved DatasetSpec

**Status:** Accepted, 2026-11-05

**Context:** Uploads can be anything: title rows, mixed date formats, numbers such as `25000k`. A fixed
schema cannot cover them, and hard-coded DataMate rules would break generality.

**Decision:** The LLM proposes a declarative `DatasetSpec` from a profile and the user's description.
Code validates it by dry-run, and the user approves it. Values that fail to parse go to quarantine.

**Consequences:**
- Works on unseen data without code changes.
- A spec is data, so a manipulated proposal cannot run code.
- Approval adds a step for users. Evals auto-approve.
- Spec quality depends on the model and needs its own measurements.

**Rejected:**
- Canonical retail schema: simple, but fails the arbitrary upload goal.
- LLM-written cleaning code for each upload: more flexible, but harder to audit and must run sandboxed.
- Letting the agent clean data at query time: repeats work and gives inconsistent answers.
