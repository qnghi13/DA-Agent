# 0046: The UI offers Vietnamese next to English, from typed dictionaries in the web

**Status:** Accepted, 2026-11-22. Design: [Vietnamese UI option](../design/2026-11-22-vietnamese-ui.md).

**Context:** The UI was English only, by an earlier call ("users non-technical first; UI in
English"). The agent already answers in the asker's language and repairs Vietnamese shorthand and
Telex (`agent/vietnamese.py`), so a Vietnamese user still read English everywhere around the
answer: buttons, "How it was calculated" (ADR 0035 left its wording "in English for now; translation
is a UI concern"), suggested questions, version review, and server errors.

**Decision:**
- **English stays the default; Vietnamese is an option.** No browser detection. The choice is per
  browser (`localStorage` key `da-agent-language`), like the theme, and falls back to English when
  storage is blocked. The switch sits next to the theme toggle, so it is there before signing in.
- **Typed dictionaries, no library.** `web/src/i18n/en.ts` defines the shape (`Messages = typeof
  en`); `vi.ts` is typed against it, so a missing or extra key fails `tsc`. Values that need data
  are functions, so word order belongs to each language ("number of different X" against "số X khác
  nhau"). Pure modules take the dictionary as a parameter; components read it with `useT()`.
- **Text is computed at render time from codes,** never stored translated, so switching changes
  the screen without a reload.
- **Errors carry codes.** Errors a user hits in normal use (upload limits and types, dataset not
  ready or changed, version open or at its limit, rate and budget limits, not found) keep their
  English `detail` and add `code` and `params` (`src/da_agent/api/errors.py`). The web translates
  known codes and shows `detail` for unknown codes or missing params. Other errors stay English.
- **What stays English:** data and column names, the agent's own answers (which follow the
  question), HTML reports (later), and the operator dashboard.
- **A test keeps screens clean:** `web/src/i18n/literals.test.ts` fails on capitalised English in a
  screen file outside the dictionaries.

**Consequences:**
- Every new UI string goes into both dictionaries; the type check enforces it.
- Server code adding a user-facing error should use `coded(...)` and add the code to both
  dictionaries; without it, the user sees English.
- Rejected: `react-i18next` (plurals and tooling two languages do not need; Vietnamese has no
  plural forms) and server-side translation by `Accept-Language` (most visible text lives in the
  web; many endpoints to touch).
