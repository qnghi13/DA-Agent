# 0037: Other readings of a business word are not offered with the answer

**Status:** Rejected, 2026-11-19. Built on a branch and removed before merge. Follows
[0036](0036-metric-definitions.md).

**Context:** After metric definitions (ADR 0036), the next step was to help people who have not
saved one: when a question uses a business word such as "doanh thu" and the data allows several
readings (which order statuses count, order lines or order totals), the answer would show the
reading it used and up to three other readings with their numbers, each saveable as the word's
definition in one click. Asking first, before any number, was rejected with the user: a person
who does not work with data waits twice and sees no number to judge.

**What was built:** a run flag on in product runs and off in eval, so eval prompts and tools stayed
byte-identical (checked by hashes); three optional `submit_answer` fields (`term`, `reading`,
`alternatives`); code that kept an alternative only when its number traced to a query other than
the answer's evidence and differed from the answer; an API view explaining each kept reading; and
a web block under the answer. About 1,300 tests passed.

**Why it was removed:** it depends on the model noticing the ambiguity. On real product questions
with Groq `gpt-oss-20b` (4 runs, about $0.002):
- A system prompt paragraph asking for other readings: no run offered any. On a dataset with
  cancelled and refunded orders and no revenue definition, the model filtered
  `status IN ('completed', 'Completed')` and answered.
- A note added by code when the plan flags ambiguity: the plan did not flag it; it chose the
  status filter with confidence in its first step.

Making it work would need code to detect the ambiguity itself (a filter on a low-cardinality
column in the answer's SQL) and an extra graph node that sends the model back for one more turn.
That adds graph complexity for one kind of ambiguity, and the result is still hard to read for
the people it is meant for.

**Decision:** do not offer other readings. Metric definitions (ADR 0036) stay: saving and matching
are done by code from SQL that ran, and they moved a 20-question check from +1 to +7.

**Lessons:**
- A feature that needs the model to notice something on its own should be tried on real
  questions with the target model before building the rest (API, web). A one-hour spike with the
  prompt alone would have shown the same result.
- A small model's plan step reports ambiguity rarely: it resolves it silently with a plausible
  default, which is the same behaviour that causes business-rule mistakes.
- Keep routing in code (agent-architecture rule): when a behaviour matters, code should decide when
  it happens, and the model only how.
