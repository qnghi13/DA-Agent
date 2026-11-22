# Vietnamese UI option

**Status:** done, merged to `main` (2026-11-22), ADR 0046. Size: a medium change, mostly mechanical.

## Intent

The web UI is English only. The agent already answers in the asker's language and expands
Vietnamese shorthand and Telex (`agent/vietnamese.py`), so what a Vietnamese user still reads in
English is the interface around the answer. Add Vietnamese as an option. English stays the
default. This reverses the earlier "UI in English" call only in that Vietnamese is now offered
too; record it in ADR 0046.

## Goals

- A language choice (English, Tiếng Việt) the user can switch at any time without reloading.
- Every string the web shows on the user screens comes from a dictionary, including "How it was
  calculated" (`explain.ts`), suggested questions (`suggestions.ts`), version review and the
  version divider (`VersionReview.tsx`, `versions.ts`), and number and date formatting.
- The server errors users actually hit can be translated.

## Non-goals

- HTML reports (`reports/html.py`): later.
- Changing how the agent picks its answer language. It keeps answering in the question's
  language whatever the UI language is.
- Translating column names, values, or anything from the uploaded data.
- More than two languages. The design must not block a third, but nothing is built for one.

## Anti-goals

- A Vietnamese screen with English fragments in the common paths (upload, ask, answer, add files).
- A missing translation that only shows up at run time.
- A new dependency.

## Approaches

- **A. Hand-written typed dictionaries in the web (picked).** `en.ts` is the source of keys;
  `vi.ts` is typed against it, so a missing key fails `tsc`. No dependency, follows the
  `theme.ts` pattern, and `explain.ts` already keeps all its wording in one file.
- B. `react-i18next`: plurals, lazy loading, and translator tooling that two languages do not
  need (Vietnamese has no plural forms). Two dependencies for little use.
- C. Server-side translation by `Accept-Language`: covers server text and reports, but most
  visible text lives in the web, and it touches many endpoints.

## Decided

- **Choice storage:** per browser, like the theme: `localStorage` key `da-agent-language`,
  wrapped in try/catch, falling back to English. Default is English, with no browser detection.
- **Switch:** in the account menu, next to the theme toggle.
- **`<html lang>`** follows the choice (screen readers, line breaking).
- **Server errors:** the response stays backward compatible. `detail` stays an English string.
  Errors a user can trigger in normal use (upload size and count, dataset not ready, version
  review refusals, rate limit, sign-in) also carry a stable `code` and the `params` the message
  needs. The web translates known codes and shows `detail` as is for any other error.
- **Suggested questions** are built in the UI language, so clicking one asks in that language and
  the agent answers in it.

## Builder's calls (marked so the user can check them)

- **Number and date formatting** follows the UI language through one locale helper (`en-US` or
  `vi-VN`). In Vietnamese, `1.234,5` and `tháng 3/2026`. Replaces the hard-coded `"en-US"` and
  the bare `toLocaleString()` calls.
- **The ops dashboard stays English.** It is an operator screen full of terms like p95 and SLO.
- **Dictionary shape:** nested objects by screen; values are strings or small functions for
  interpolation (`(rows: number) => string`), so no template mini-language.
- **How the language reaches components:** a React context with a `useT()` hook. Pure modules
  (`explain.ts`, `suggestions.ts`, `versions.ts`) take the dictionary as an argument, so their
  tests run without React.
- **Tests:** language storage like `theme.test.ts`; `explain`, `suggestions`, and `versions`
  tested in both languages; the server tests check `code` on each coded error.
- **Docs:** ADR 0046, learning note 25 (Vietnamese), progress update.

## Left to the builder

- The exact list of coded server errors, from a pass over `api/app.py` and `api/versions.py`.
- Vietnamese wording. Prefer the plain terms a non-technical user knows ("tải lên", "phiên bản",
  "cách tính").
