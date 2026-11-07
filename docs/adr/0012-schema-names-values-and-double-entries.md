# 0012: Keep source column names, list known values, and remove proven double entries

**Status:** Accepted, 2026-11-07

**Context:** The first baselines on the `bootstrap` set (one pharmacy, 30 questions) answered 22
questions and got 6 right. Error analysis on dev answers found three causes that are not specific
to that shop:
- Shop documents name columns as the upload does (`TriGia`), while clean tables used snake_case
  (`tri_gia`). The agent copied names from the documents and got binder errors on most runs.
- Status values are stored in English (`cancelled`), while the rules say `huỷ`. The agent guessed
  values in the question's language.
- Order lines held 1,208 rows that repeat another row exactly, with the same order id and line
  number. They inflated every sum by about 2%. Seven of 24 dev shops have exact repeats; in four
  of them a key of one or two columns identifies every distinct row. No spec turned on `dedupe`.

**Decision:**
- A column whose raw name is already an identifier (`[A-Za-z][A-Za-z0-9_]*`) keeps it, unless
  another column has that name ignoring case. Other columns still get a snake_case name. This
  applies to proposed specs, saved specs rebuilt without the model, and columns missing from a spec.
- The build report lists every distinct value of a text column with at most 12 values of at most
  60 characters. `describe_dataset` shows them, and the source name of a renamed column.
- The build counts exact repeats in each clean table and searches for a key: at most two columns,
  fewer than the table has, with no blanks, that tell every distinct row apart. When a key exists,
  code turns on `dedupe: exact` for that table and rebuilds, for both new and saved specs. Tables
  with repeats and no key keep them, and the prepare summary reports them.

**Consequences:**
- Documents, questions, and SQL use the same names, and DuckDB matches unquoted names ignoring
  case, so both `TriGia` and `trigia` work.
- Known values go into every prompt that describes the dataset. They are cell values, so they are
  data in a JSON tool result, short, and never from hidden columns.
- Code removes rows without asking the model or the user. This is safe only because a key proves
  the repeats are copies: identical rows that share an order id and line number cannot be two sales.
- A sales log with no ids can hold two identical real sales. Without a key the repeats stay, even
  when they are double entries; three dev shops are in this case.
- The key search runs up to 820 distinct counts on tables with repeats, a few seconds on 100k rows.

**Rejected:**
- Asking the model to choose `dedupe` from a repeat count: it already saw the profile and never
  chose it, and code can prove the case that matters.
- Removing all exact repeats: wrong for sales logs without ids.
- Renaming documents to match snake_case names: documents are free text that users write.
