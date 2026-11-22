# 0047: Code picks the answer language from the question; the model is told it

**Status:** Accepted, 2026-11-22. Follows [0046](0046-vietnamese-ui.md).

**Context:** ADR 0046 assumed the agent "answers in the asker's language", but no prompt said so.
A real run on `gpt-oss-20b` answered a Vietnamese question with an English `reason` ("The month
with the highest total quantity…"). The model writes three kinds of text a user reads: the
`reason` in `submit_answer`, the `clarify_question` of an ambiguous plan, and the plan `steps`
shown under Show work.

**Decision:**
- **Code decides the language** (`question_language` in `agent/vietnamese.py`), and the prompt
  gets one plain line only for Vietnamese. English, the default, adds nothing, so English runs see
  the same prompts as before. A question is Vietnamese when it has a letter only Vietnamese uses
  (ă â đ ê ô ơ ư, the toned vowels U+1EA0–U+1EF9, ĩ ũ), a word with a tone mark that is a
  Vietnamese syllable without it ("tháng", not "Café"), or Vietnamese-only shorthand ("hqua").
- **Translated:** `reason` (answer prompt) and `clarify_question` (plan prompt). **Kept:** plan
  `steps`, which the execute step reads back, so changing them could change accuracy; the answer
  value, names, column names, and data values, which the scorer and the user compare with the data.
- The UI language plays no part: the answer follows the question (ADR 0046).
- Rejected: telling the model to "answer in the question's language" (a soft instruction a small
  model often ignores, and not testable), and following the UI language (contradicts ADR 0046).

**Consequences:**
- Unaccented Vietnamese, even typed as Telex, is read as English and answered in English. The
  Telex decoder also rewrites English words ("is" to "í", "How" to "hơ"), and a count of valid
  syllables cannot tell "doanh thu thang nay" from "can i get the data". Revisit with a word list
  if users type without accents often.
- **Not measured on the eval set** (the user chose not to spend on it). DataMate questions are
  Vietnamese, so every eval run now carries the extra line. The scorer ignores `reason`, but the
  line could still shift behaviour; compare totals at the next eval run.
- Model-written column descriptions in the dataset spec stay English (ingestion, not per
  question).
