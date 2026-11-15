# Suggested questions from the dataset's own columns

**Status:** implemented. Small change, no ADR.

## Why

A person who does not work with data opens an empty chat and does not know what to ask. The empty
state showed three fixed questions, the same for every dataset ("What are the totals by month?"),
which often did not fit the data.

## Goals and non-goals

- Up to five questions that name the dataset's real columns, by the names in the uploaded file.
- No model call: the web builds them from the spec (column types, sensitive flags) and the column
  profile (distinct values), which it already has. No backend change.
- A sensitive column is never named. A column whose values never repeat (an ID) is never ranked.
- Clicking a question fills the composer; the person sends it. Sending costs a model call, so a
  click alone does not.
- Non-goals: follow-up suggestions after an answer (needs a model), translation (the UI is
  English for now).

## Approaches

| Approach | Trade-off |
|---|---|
| **A. Templates over column roles, in the web (chosen)** | Free, instant, always names real columns. Plain wording, a fixed set of shapes |
| B. A model writes questions during the build | Natural and varied, but a model call per build, may name a column that does not exist or a hidden one, and old datasets get none |
| C. Templates in the API | Same result as A with an extra endpoint; the web already holds the spec and profile |

## Design

1. Main table: the one with both a date and an amount column, then the one with more rows.
2. Roles among visible columns: date (`date`, `datetime`); amount, the column whose sum means
   something: a quantity (`quantity`, or an `integer` named like "số lượng", "SL", "qty"), then
   `money` that is not named like a unit price ("giá", "đơn giá", "price" without "tổng",
   "thành tiền", "total", "amount", "doanh thu"), then `number`. Names are compared without
   accents. Types alone were not enough: the first browser check found a demo shop whose quantity
   was typed `integer` and whose unit price was `money`, so it suggested summing unit prices.
   Status: text with 2–10 distinct values; category: text with more than 10 distinct values that
   repeat. Both need the profile, so datasets built before it get date and amount questions only.
3. Questions, in order, when their roles exist: total by month; top 5 category by total; records
   per status; month with the highest total; date range. With no date: the total. With no role:
   the three general questions.
