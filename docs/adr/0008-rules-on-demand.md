# 0008: Topic rules fetched on demand

**Status:** Accepted, 2026-11-06

**Context:** The plan put the whole rulebook in the prompt and deferred retrieval to Days 14–15,
expecting prompt caching to make the repeated text cheap. Measured on Groq `gpt-oss-20b`, the
rulebook was about 4,700 of the 5,800 tokens in the first call and was resent on every turn, and
Groq returned no cached tokens in practice. It was the largest cost of every call.

**Decision:** A `Document` can be marked on demand. The prompt shows its preface and level-2 section
titles, and the `search_rules` tool returns up to two sections, matched by title, number, or
keywords, without diacritics. There are no embeddings: rule documents are short and well structured.
The eval adapter keeps the sections that shape every answer inline (unanswerable, file conventions,
answer format, glossary) and makes topic sections (revenue, time, ranking, debt, stock, industry) on
demand.

**Consequences:**
- The system prompt for a DataMate shop shrinks from about 17,100 to 8,100 characters.
- A model that skips a lookup can apply the wrong definition. Evals compare it with the inline
  baseline, and a later `verify` check can require a lookup for questions that use a defined term.
- One extra tool call per question, which the iteration limit must allow.

**Rejected:**
- Embedding retrieval: adds a model and a store for ten sections, and is harder to debug.
- Whole rulebook inline: simplest, but costs about 3,000 extra tokens on every call while caching
  does not apply.
- Code chooses sections from the question: needs a classifier and still misses implicit rules.
