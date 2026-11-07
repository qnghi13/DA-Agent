# 0014: Verification probes the data for join fan-out and unreadable values

**Status:** Accepted, 2026-11-07

**Context:** ADR 0013 checks that an answer's numbers come from a query and that its period has
data. Two common errors pass both: a join that repeats the rows being summed, and a sum over a
column whose unparsable values are empty in the clean table. Neither can be seen in the SQL text
alone; both depend on the data.

**Decision:**
- For each SELECT in the queries behind an answer, `verify` runs a read-only probe with the same
  FROM, JOIN, and WHERE. Filters, anti-joins, and periods are respected because they are part of
  the probe. At most 4 probes per check, about 0.7 s each in the sandbox.
- Fan-out is measured, not guessed from key names: `count(t.rowid)` against
  `count(DISTINCT t.rowid)` per table. An aggregate fails only when every table it reads repeats,
  so a lookup price repeated per line passes. It goes back to the loop as a repair.
- Unreadable values: the build keeps `_source` and `_row` on clean rows and lists failed values in
  `_quarantine` by the same keys. The probe counts such rows inside the query's rows. A hit stops
  the run with `unreadable_values` and the count, without a repair: the model cannot fix the
  data, and a sum missing one row is wrong.
- A probe that errors is skipped. The checks guard; they do not gate on their own failures.

**Consequences:**
- Answers over rows with unreadable values are withheld even when the share is tiny. The reason
  names the column and count, so the user can fix the spec. An interactive run might later answer
  with a caveat instead; that would be a new decision.
- Verification now runs SQL, so it costs sandbox time but no tokens. Replayed on 70 saved graph
  runs: 28 probes, no correct answer flagged. The 8 unreadable dev values are all on return rows,
  which the agent's queries exclude.

**Rejected:**
- Uniqueness of join keys from metadata: the spec does not declare keys, and a key that is unique
  overall may not matter after the query's filters.
- Repair for unreadable values: the model could only guess the missing numbers.
- Warning without withholding in eval runs: the scorer counts a wrong number as −1.
