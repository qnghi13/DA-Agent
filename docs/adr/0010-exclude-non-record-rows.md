# 0010: Declarative rules exclude subtotal and note rows

**Status:** Accepted, 2026-11-06

**Context:** Sales sheets often hold rows that are not records: daily subtotals ("Tổng ngày 01/03"),
monthly totals, and notes ("Ghi chú: số liệu ghi tay"). Left in, they double count every `SUM`.
Four of the 24 dev shops have them, and one more holds totals and notes at the end of each monthly
sheet. Keyword filters do not work: in three other shops "Total" is a tool brand in product names.

**Decision:** `TableSpec.exclude_rows` holds declarative rules. A rule is a list of conditions on raw
columns (`blank`, `not_blank`, `equals`, `starts_with`, `contains`; trimmed and case-insensitive)
that must all hold, with a reason. Rules run on raw values before fill-down and dedupe. Excluded
rows go to `_excluded` with their reason, source, row, and raw values without sensitive columns.
To help the model find these rows, the raw profile reports rare row shapes: sets of filled columns
under 5% of a table and sparser than the common one, with two example rows.

**Consequences:**
- Works on unseen layouts, because row shapes need no language or keyword list.
- Rules are data compiled with quoted identifiers and escaped literals, and have no regex, so a
  manipulated proposal cannot inject SQL or run a slow pattern.
- A wrong rule can drop real records. `_excluded` keeps them for review, and a rule that removes
  more than 10% of a table goes back to the model as a problem.
- Exclusion runs before fill-down, so a subtotal row never fills the rows below it.

**Rejected:**
- Fixed keyword list ("tổng", "total"): language-specific, and it removes products named "Total".
- Detecting subtotals in code alone: row shapes find candidates, but deciding whether a sparse row
  is a record needs judgment about its values.
- Regex conditions: more expressive, but harder to audit, and the listed operations cover every
  case in the dev data.
