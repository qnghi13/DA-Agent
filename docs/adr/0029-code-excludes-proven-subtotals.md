# 0029: Code excludes subtotal rows it can prove

**Status:** Accepted, 2026-11-11. Narrows ADR 0010's rejection of detecting subtotals in code.

**Context:** ADR 0010 leaves subtotal rows to rules the model writes, with a code check that sends
missed subtotals back as a problem. On an unseen shop, a model wrote a rule that was too narrow: it
required the debt cell to read "0đ", so 240 daily and monthly totals with other debt values stayed
in the order table, and three repair rounds did not fix it. Every revenue sum on that shop would
count those totals. Smaller local models, which the offline deployment needs, will miss more often.

**Decision:** after the spec's rules mark rows, the build also excludes each row that it can prove
is a subtotal, with the reason "Proven subtotal: equals the sum of the rows above (code)":
- The row has a rare shape that ADR 0010 already reports as subtotals (most of its rows, and at
  least two, equal the sum of the rows above them).
- One of its numbers is non-zero and equals the sum of that column over the record rows above it,
  back to the previous row of the shape in the same source, and that sum covers **at least two**
  record values.
- Rounds repeat, up to three, so totals over totals are proven too. In each round a group ends
  either at any row of the shape (a day's total, whatever rule removed its neighbours) or only at
  rows still in the table (a month's total over days already removed).

Rows a rule already excludes keep the rule's reason. The build report counts the code's rows per
table (`proven_subtotals`).

**Consequences:**
- The narrow rule above now yields the same clean table as a correct rule: 25,191 rows, no total
  left. On the 24 training dev shops the saved specs already catch every subtotal, so the code
  excludes nothing more, and exclusion precision and recall stay 100%.
- Hand-written totals that do not match their rows are still left to the model's judgment, as
  ADR 0010 describes.
- Risk: a real record of a rare shape whose amount equals the sum of two or more records above it
  is excluded. The shape must already look like subtotals, and the row stays in `_excluded` with
  its raw values for review.

**Rejected:**
- Proving with one record above: a sale often repeats the amount of the sale before it.
- Only one way to end a group: ending at every row of the shape misses monthly totals; ending
  only at rows still in the table merges days when a rule removed some daily totals but not others.
- Letting more repair rounds fix it: costs model calls and still depends on the model.
