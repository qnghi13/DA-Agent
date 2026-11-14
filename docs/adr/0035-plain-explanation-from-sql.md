# 0035: Explain an answer in plain words by reading its SQL in code

**Status:** Accepted, 2026-11-15.

**Context:** The project serves people who do not read SQL first. An answer shows a number and the
SQL behind it; such a person cannot check which rows were counted or which period was summed. An
explanation written by the model would read well, but it costs tokens on every answer, changes the
prompt, and can describe a filter the SQL does not apply. That last risk defeats the purpose.

**Decision:**
- Code reads the answer's evidence queries with sqlglot and returns structured facts: tables and
  joins, measures, filters, periods, grouping, top-N. The web writes the sentences, in English for
  now; translation is a UI concern.
- Parts code cannot put in words (subqueries, CTEs, window functions, set operations, unknown
  functions) are listed, and the explanation is marked incomplete. A partial reading is never shown
  as the whole.
- Column names show as in the uploaded file, from the dataset spec.
- Computed by the API on read. Parsing runs no SQL; old runs get explanations with no migration.
- The date-bound reader in `verify` is shared, so the period shown is the period the check uses.

**Consequences:**
- No model cost, no eval run, and the agent is unchanged.
- Phrasing is plain and covers common shapes. Coverage is measured on local eval SQL.
- The groundwork for metric definitions: an answer can later say which saved metric it used.

**Rejected:**
- The model writes the explanation: cost, an eval run, and text that may not match the SQL.
- Code extracts, a model phrases: still a model call per answer; revisit if the plain text reads
  too stiffly.
- Computing at run end and storing it: old runs would have none, and a wording change would need a
  backfill.
