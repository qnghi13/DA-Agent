# 0052: Sheet layouts: code finds the structure, the model picks operators, code checks the result

**Status:** Accepted, 2026-11-27. Design: [layout layer](../design/2026-11-27-layout-layer.md).
Plan: [layout layer plan](../design/2026-11-27-layout-layer-plan.md). Results:
[layout eval](../eval/layout.md).

**Context:** Raw ingestion assumed one sheet is one table under one header row. A private set of
14 shops broke that assumption in 8: headers in two or three rows, day numbers as headers, context
in titles and sheet names, section rows, two tables side by side, `key: value` lines down one
column, calendar cells holding several records, time-in and time-out rows, and headers that drift
across sheets. Prior art agrees on two points. Prompted models are poor at finding table
boundaries in a raw grid (SpreadsheetLLM: GPT-4 in-context table detection F1 0.15 to 0.41), so
code should find regions. And reliable systems describe structure in a small fixed language and
check the output (Auto-Tables' operators and input-output reranker; SpreadsheetAgent's structure
sketch with a verifier). ADR 0006 already keeps the spec declarative, and ADR 0051 showed our
target model misuses free code.

**Decision:**
- **Code reads the grid first** (`grid.py`, `layout_findings.py`): regions are connected groups
  of filled cells, with titles and footers within two rows joined to their table and blocks
  without a header joined to the table above. Row roles are title, header layers, section, total,
  footer, repeated header, and data. Sheets whose headers match after case, accents, and order (one
  or two columns may be added) form a family.
- **Only flagged families reach the model.** Flags: several tables, several header rows, day
  numbers as the header, section rows, one filled column, packed cells, drifting headers,
  repeated headers, a header the fast path would miss, title values that differ between the
  family's sheets, and sheet names that name things rather than months. A constant title (report
  metadata) does not flag. No family of the 24 older dev shops is flagged: they keep today's path
  with no model call and the same raw tables.
- **The model answers with a `LayoutSpec`** (`layout_spec.py`), data from a fixed set of
  operators: region, header layers with a spread direction, context (sheet name, file name, a
  cell, the cell after a label), sections, excluded row roles, and a reshape with an optional
  second step: `unpivot`, `split_groups`, `pair_rows`, `record_blocks`, `split_cell`. Sizes are
  bounded and there is no code or pattern. `check_layout` drops entries naming families, regions,
  rows, or columns that do not exist.
- **Code applies and judges** (`layout_apply.py`, `propose_layout.py`):
  - **Coverage:** every filled cell must land once, in a record, a header, a context or section
    value, or an excluded row.
  - **Shape checks**, because coverage cannot tell a tidy table from a wide one. Day columns left
    side by side, the same sub-columns under several group labels, a column of `key: value`
    lines, a constant context cell beside one that changes, and a reshape that reports a problem
    each cost the candidate and go back to the model in words.
  - **Operators refuse rather than pass rows through:** groups on the column-name layer, a record
    key no line has, and keys that repeat inside a record are reported.
  - **Ranking:** the score is the mean coverage over every flagged family, minus penalties for
    columns that mix kinds of values and for untidy families. Two candidates first, then up to
    two repair rounds of the best one.
- **Provenance:** in a layout table `_row` is the record's ordinal in its source and `_cell` is the
  sheet range it came from. Build keeps `_cell` in clean tables and `_excluded`.
- **The layout is part of the approved spec** (`DatasetSpec.layout`), because the product ingests
  again on approval and on added files. Only the layout stage sets it. Family ids are hashes of
  the family's shape, so a stored layout still finds its family after files are added.
- **A stronger model for this step:** `DA_AGENT_INGEST_ROUTES`, the question routes when unset.
  Groq `openai/gpt-oss-120b` costs $0.15 per million input tokens and $0.60 per million output
  tokens. Layouts run once per upload; questions keep the small model.
- **Fallback:** when no candidate wins, or the model fails, the upload keeps the fast path with a
  warning.

**Measured** (2026-11-27, $0.15 in all; `docs/eval/layout.md`):
- **Dev**, 7 shops, 49 hand-written targets:
  - code only: 30;
  - hand-written layouts: 48;
  - the model: 41 at first and 48 twice at the end, over seven runs ($0.123).
- **Held-out**, 7 shops, 47 targets, run once: code only 33, the model 38 ($0.026). The misses:
  - a timesheet split into one region per department;
  - a calendar read with the wrong operator;
  - single-cell records taken as sections;
  - records in purchase slips taken as non-records;
  - a total written as `128tr450`.

  `pair_rows` and `split_cell`, needed only by held-out shops, never succeeded.

**Rejected:**
- **The model finds regions in the raw grid:** weak in the literature, and code does it well.
- **Free code or regex from the model:** ADR 0006 and ADR 0051.
- **Coverage as the only score:** in the first dev run it rated a wide attendance table, a label
  taken as a value, and a failed reshape as perfect.
- **A trained cell-role classifier** (as in arXiv 2609.20732): no labelled data of our own yet.
- **A preview for the user to approve layouts:** left for later; evals approve automatically.

**Follow-up** (2026-11-28): the timesheet and calendar misses were reproduced on a synthetic
upload and fixed in code: clock times are not header text, a `paired_rows` flag with a hint, shape
checks that ask for `pair_rows` and `split_cell`, header names with a slash, and paired day columns.
The model then read the synthetic upload 3 of 3; dev stayed 48 of 49. Held-out was not rerun.

Later the same day the two other misses were fixed the same way. A one-cell row is a section only
under a header of three or more columns; compact millions are numbers; a table's last row of a few
numbers under blank leading cells is its total. Synthetic upload 6 of 6, dev 48 of 49.

**Next:** the per-period summary lines of a payment statement (no label, not a sum), the one dev
target both hand-written layouts and the model miss; and a held-out-like check on new private
shops, since this held-out set has been used.
