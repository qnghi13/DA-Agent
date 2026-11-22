# Vietnamese UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Vietnamese as a UI language option; English stays the default.

**Architecture:** Typed dictionaries in the web (`en.ts` defines the shape, `vi.ts` must match it),
a React context for the current language stored per browser, and pure modules that take the
dictionary as an argument. The API keeps its English `detail` and adds a stable `code` and `params`
on errors users hit, which the web translates.

**Tech Stack:** React 19 + TypeScript + Vitest (web), FastAPI + pytest (API). No new dependency.

**Spec:** `docs/design/2026-11-22-vietnamese-ui.md`

## Global Constraints

- English is the default; no browser language detection.
- Choice stored in `localStorage` key `da-agent-language`; every read and write in try/catch,
  falling back to English.
- No new dependency (web or Python).
- `detail` stays an English string on every error response; `code` and `params` are additions.
- The agent's answer language is not touched. Column names, values, and uploaded data are never
  translated.
- Out of scope: HTML reports (`src/da_agent/reports/html.py`), the ops dashboard
  (`web/src/OpsDashboard.tsx`, `web/src/ops.ts`), Vega chart number formatting.
- Number and date formatting follow the UI language: `en-US` or `vi-VN`.
- Vietnamese copy uses plain words a non-technical user knows ("tải lên", "phiên bản", "cách tính").
- Branch `feat/vietnamese-ui`. Commit only with the user's go-ahead (CLAUDE.md).
- Web checks: `cd web && npm test && npm run typecheck`. Python: `uv run pytest`, `uv run ruff
  check .`; integration tests need `uv run --env-file .env pytest`.

## Review Focus

1. **Blocked storage** (private window): the page loads in English and switching still works
   for the session. Test in Task 2.
2. **Unknown `code` or missing `params`** (old web, newer server, or the reverse): the web shows
   the English `detail`, never `undefined` or a blank. Test in Task 2.
3. **Switching language mid-run:** activity text, status badges, and stop reasons re-render in
   the new language, because they are computed from codes at render time, not stored as text.
   Test in Task 3 (`activity`, `stopReasonText` give different text for `en` and `vi` from the
   same trail). Error strings already in component state may stay in the old language until the
   next action; that is accepted.
4. **Long Vietnamese labels at phone width** (375 px): no clipped buttons or overflowing menus.
   Checked by screenshot in Task 5.
5. **`<html lang>`** follows the choice, so screen readers switch voice. Test in Task 2.

---

### Task 1: Error codes on the API

**Files:**
- Create: `src/da_agent/api/errors.py`
- Modify: `src/da_agent/api/app.py` (raise sites listed below; `create_app` installs the handler)
- Modify: `src/da_agent/storage/uploads.py:18-29` (`UploadRejectedError` carries `filename`, `allowed`)
- Test: `tests/unit/test_api_errors.py` (new, no Postgres), `tests/integration/test_api.py`,
  `tests/integration/test_limits.py`

**Interfaces:**
- Produces: response body `{"detail": str, "code": str, "params": dict[str, str | int | float]}`
  for coded errors; any other error keeps today's body. The web (Task 2) relies on these codes:

| code | status | params | today's raise site in `app.py` |
|---|---|---|---|
| `not_found` | 404 | — | `_not_found()` |
| `upload.too_large` | 413 | `file` | `_save` |
| `upload.too_many_files` | 413 | `max` | dataset upload, add files |
| `upload.unsupported_type` | 400 | `file`, `allowed` | both `UploadRejectedError` catches |
| `dataset.not_ready` | 409 | `status` | add files, and the two other "not ready" sites |
| `dataset.changed` | 409 | — | `CHANGED_DATASET` sites |
| `export.expired` | 404 | — | `EXPIRED_EXPORT` sites |
| `version.already_open` | 409 | — | both "a version is already open" sites |
| `version.limit` | 409 | `max` | the version-count check |
| `limit.sign_in` | 429 | — | failed sign-ins |
| `limit.requests` | 429 | — | per key or session |
| `limit.jobs` | 429 | — | new jobs per workspace |
| `limit.budget` | 429 | `limit` (number, dollars) | daily model budget |

- In `errors.py`:
  `class CodedHTTPException(HTTPException)` with `code: str` and `params: dict`;
  `def coded(status: int, code: str, detail: str, params: dict | None = None, headers: dict[str, str] | None = None) -> CodedHTTPException`;
  `def install_error_handler(app: FastAPI) -> None` registers a handler for `CodedHTTPException`
  that keeps `headers` (Retry-After must survive).
- `_too_many(wait, detail)` becomes `_too_many(wait, code, detail, params=None)`.

- [ ] **Step 1: Write the failing unit tests** in `tests/unit/test_api_errors.py`, on a tiny
  FastAPI app with `install_error_handler` and one route per case:
  - `test_a_coded_error_keeps_detail_and_adds_code_and_params`: raising
    `coded(413, "upload.too_many_files", "at most 20 files", {"max": 20})` gives status 413 and
    body `{"detail": "at most 20 files", "code": "upload.too_many_files", "params": {"max": 20}}`.
  - `test_a_coded_error_without_params_sends_an_empty_object`: `params == {}`.
  - `test_headers_survive`: `headers={"Retry-After": "7"}` arrives on the response.
  - `test_plain_http_exceptions_are_unchanged`: `HTTPException(400, "x")` gives `{"detail": "x"}`.
- [ ] **Step 2: Run them, expect FAIL** (`ModuleNotFoundError: da_agent.api.errors`).
  Run: `uv run pytest tests/unit/test_api_errors.py -v`
- [ ] **Step 3: Implement `errors.py`**, and give `UploadRejectedError.__init__(self, message: str, *, filename: str, allowed: str)`.
- [ ] **Step 4: Run the unit tests, expect PASS.**
- [ ] **Step 5: Write failing integration assertions** (extend, don't duplicate):
  - `test_bad_uploads_are_refused_and_leave_nothing_behind` gains an expected `code` per case
    (`upload.unsupported_type` for a bad suffix, `upload.too_many_files`, `upload.too_large`;
    `None` for the 422 case, which has no `code` key).
  - `test_limits.py`: `test_a_key_over_its_request_rate_is_told_to_wait` asserts
    `code == "limit.requests"`; `test_repeated_wrong_keys_from_one_address_are_slowed` asserts
    `limit.sign_in`; `test_new_jobs_have_their_own_lower_rate_per_workspace` asserts
    `limit.jobs`; `test_a_workspace_over_its_daily_budget_cannot_start_jobs` asserts
    `code == "limit.budget"` and that `params["limit"]` is a number.
  - `test_api_versions.py`: `test_a_second_open_version_is_refused_and_leaves_no_folder` and
    `test_an_open_version_is_refused_before_any_file_is_saved` assert `version.already_open`;
    `test_a_dataset_that_is_not_ready_takes_no_files` asserts `dataset.not_ready` and
    `params["status"]`; `test_the_version_limit_returns_409` asserts `version.limit` and
    `params["max"]`; `test_another_workspace_gets_404_on_every_route` asserts `not_found`.
- [ ] **Step 6: Run them, expect FAIL** (`KeyError: 'code'`).
  Run: `uv run --env-file .env pytest tests/integration/test_api.py tests/integration/test_limits.py tests/integration/test_api_versions.py -v`
- [ ] **Step 7: Switch every raise site in the table to `coded(...)`, with today's `detail` text
  unchanged.** Call `install_error_handler(app)` in `create_app`.
- [ ] **Step 8: Run the full suite and lint, expect PASS.**
  Run: `uv run --env-file .env pytest && uv run ruff check . && uv run ruff format --check .`
- [ ] **Step 9: Commit** `feat(api): stable error codes for errors users hit`.

### Task 2: Language core in the web

**Files:**
- Create: `web/src/i18n/en.ts`, `web/src/i18n/vi.ts`, `web/src/i18n/language.ts`,
  `web/src/i18n/context.tsx`, `web/src/i18n/errors.ts`
- Create tests: `web/src/i18n/language.test.ts`, `web/src/i18n/errors.test.ts`,
  `web/src/i18n/literals.test.ts`
- Modify: `web/src/api.ts:9-16,630-660` (`ApiError` carries `code`, `params`; `errorDetail`
  parses them; `errorMessage` moves to `i18n/errors.ts`), `web/src/main.tsx` (provider),
  `web/src/components/account-menu.tsx` (switch, and its own strings)
- Create: `docs/adr/0046-vietnamese-ui.md` (extends the "UI in English" call; links the spec)

**Interfaces:**
- Consumes: the error codes in Task 1's table.
- Produces:
  - `en.ts`: `export const en = { locale: "en-US", ... }` and `export type Messages = typeof en`.
    Not `as const`, so values are `string` or functions. Nested by screen (`common`, `account`,
    `errors`, `explain`, `run`, `setup`, `chat`, `versions`, ...); later tasks add their sections
    to both files. Values that need data are functions, e.g. `tooLarge: (file: string) => string`.
  - `vi.ts`: `export const vi: Messages = { locale: "vi-VN", ... }`. A missing or extra key
    fails `tsc`.
  - `language.ts`: `type Language = "en" | "vi"`; `LANGUAGES: { value: Language; label: string }[]`
    with labels `English` and `Tiếng Việt` (each in its own language, never translated);
    `readStoredLanguage(storage): Language`; `storeLanguage(storage, language): void`;
    `messagesFor(language): Messages`; `applyLanguage(language, doc): void`. `Storage` type as
    in `theme.ts`.
  - `context.tsx`: `LanguageProvider`, `useLanguage(): { language: Language; setLanguage(l: Language): void }`,
    `useT(): Messages`. Setting the language stores it and calls `applyLanguage`.
  - `ApiError(status: number, message: string, code?: string, params?: Record<string, unknown>)`.
  - `errors.ts`: `errorMessage(caught: unknown, m: Messages): string`. Order: 401 →
    `m.errors.signedOut`; a known `code` → `m.errors.codes[code](params)`; 429 → `m.errors.slowDown(text)`
    wrapping either; otherwise `caught.message`. A translator that throws or a code it does not
    know falls back to the English `detail`.
  - `literals.test.ts` exports nothing; it holds `PENDING: string[]`, the screen files not yet
    translated, which Tasks 3–5 empty.

- [ ] **Step 1: Write the failing tests.**
  - `language.test.ts` (copy `memoryStorage` and `broken` from `theme.test.ts`):
    `defaults to English` (`readStoredLanguage(memoryStorage())` is `"en"`);
    `reads a stored choice` (`{"da-agent-language": "vi"}` gives `"vi"`);
    `ignores a value it does not know` (`"fr"` gives `"en"`);
    `survives blocked storage` (read gives `"en"`, `storeLanguage(broken, "vi")` does not throw);
    `gives each language its dictionary and locale` (`messagesFor("vi").locale === "vi-VN"`).
  - `errors.test.ts`:
    `translates a known code` (ApiError 413 `upload.too_many_files` `{max: 20}` gives a Vietnamese
    string containing `20` with `vi`, and with `en` contains `20` too);
    `falls back to detail for an unknown code` (`code: "new.thing"`, message `"server text"` gives
    `"server text"` with `vi`);
    `falls back to detail when params are missing` (known code, `params` undefined: no
    `"undefined"` in the output, and it is non-empty);
    `wraps 429 in slow down` (vi result starts with the Vietnamese slow-down prefix);
    `signed out by status` (401 gives `vi.errors.signedOut`);
    `errorDetail reads code and params` (a `Response` with JSON body from Task 1 gives all three).
  - `literals.test.ts`: reads every `.tsx` under `web/src` with `node:fs`, skipping
    `components/ui/`, `OpsDashboard.tsx`, test files, and `PENDING`, and fails on JSX text of two
    or more letters starting with a capital (`/>\s*[A-Z][a-z]+[^<{]*</`) or an
    `aria-label|placeholder|title|alt` attribute literal starting with a capital letter. The
    failure lists file and line. Start `PENDING` with every screen file except `account-menu.tsx`.
  - `language.test.ts` also tests `applyLanguage(language: Language, doc: Pick<Document, "documentElement">)`
    from `language.ts` (no testing library is installed, and none is added): with a stub
    `{ documentElement: { lang: "en" } }`, `applyLanguage("vi", doc)` sets `lang` to `"vi"`.
    `LanguageProvider` calls it on every change.
- [ ] **Step 2: Run, expect FAIL** (missing modules; `account-menu.tsx` literals listed).
  Run: `cd web && npm test`
- [ ] **Step 3: Implement the files above.** The switch is two `DropdownMenuItem`s (or a radio
  group) under a separator in the account menu, with a check on the current one. Wrap `<App />`
  in `LanguageProvider` in `main.tsx`. Update every `errorMessage(caught)` call site to pass
  `useT()`'s result (27 sites in `.tsx`).
- [ ] **Step 4: Run, expect PASS.** Run: `cd web && npm test && npm run typecheck`
- [ ] **Step 5: Write ADR 0046:** context (UI in English; Vietnamese users), decision (approach
  A, defaults, coded errors, what stays English), consequences (each new string needs both
  files; `tsc` enforces it). Add it to `docs/adr/README.md`.
- [ ] **Step 6: Commit** `feat(web): language choice with typed dictionaries`.

### Task 3: Pure modules in both languages

**Files:**
- Modify: `web/src/explain.ts`, `metrics.ts`, `run.ts`, `feedback.ts`, `status.ts`,
  `profile.ts`, `suggestions.ts`, `versions.ts`, and their call sites
- Modify: `web/src/i18n/en.ts`, `web/src/i18n/vi.ts` (sections `explain`, `metrics`, `run`,
  `feedback`, `status`, `profile`, `suggestions`, `versions`)
- Test: the existing `*.test.ts` of each module

**Interfaces:**
- Consumes: `Messages`, `en`, `vi` from Task 2.
- Produces: every exported function in these modules that returns user text takes
  `m: Messages` as its **last, required** parameter: `describeExpr(fact, m)`,
  `describeFilter(fact, m)`, `explanationLines(explanation, m)`, `uniqueExplanations(explanations, m)`,
  `metricSentence(facts, m)`, `readMetricName(name, aliases, m)`, `activity(trail, m)`,
  `answerSummary(..., m)`, `toolLabel(tool, m)`, `stopReasonText(reason, m)`,
  `formatAnswer(answer, m)` (replaces `locale?`), `feedbackReasons(m)` (replaces the constant
  array), `datasetStatus(status, m)`, `share(part, whole, m)`, `columnDetail(column, m)`,
  `suggestQuestions(dataset, m)`, `genericQuestions(m)` (replaces `GENERIC_QUESTIONS`),
  `statusLine(version, m)`, `earlierNewFilesText(overlap, m)`. Numbers use
  `new Intl.NumberFormat(m.locale, ...)`. Word order lives in the dictionary: where Vietnamese
  orders words differently (count, distinct, aggregates, dates), the entry is a function.

- [ ] **Step 1: Update existing tests to pass `en`** (no behaviour change), then add Vietnamese
  cases that fail:
  - `explain.test.ts`:
    sum of column `Thành tiền` → en `total of Thành tiền`, vi `tổng Thành tiền`;
    distinct count of `Mã KH` → en `number of different Mã KH`, vi `số Mã KH khác nhau`;
    count with no column → vi `số dòng`;
    period `Ngày` 2026-03-01..2026-03-31 → en `Ngày from 1 Mar 2026 to 31 Mar 2026`,
    vi `Ngày từ 1/3/2026 đến 31/3/2026`;
    `in` on `Trạng thái` with `Hủy`, `Hoàn` → vi `Trạng thái là “Hủy” hoặc “Hoàn”`;
    number `1234.5` → vi `1.234,5`;
    `explanationLines(..., vi)` labels are Vietnamese (assert the first label is `Dữ liệu`);
    `uniqueExplanations` drops the same repeat in both languages.
  - `run.test.ts`: same trail gives different `activity` text for `en` and `vi`;
    `stopReasonText("cancelled", vi)` is Vietnamese; `formatAnswer` of `1234.5` with `vi` is
    `1.234,5`.
  - `suggestions.test.ts`: for a dataset with a `Ngày` date and `Thành tiền` money column, every
    vi question names the column unchanged, ends with `?`, and equals no en question;
    `genericQuestions(vi)` has the same length as `genericQuestions(en)`.
  - `versions.test.ts`: `statusLine` for added rows `1234` → vi contains `1.234`.
  - `metrics.test.ts`: `readMetricName("", "", vi)` returns a Vietnamese `error`.
  - `status.test.ts`, `feedback.test.ts`, `profile.test.ts`: one vi assertion each (labels
    differ from en).
- [ ] **Step 2: Run, expect FAIL.** Run: `cd web && npm test`
- [ ] **Step 3: Move every user string in these modules into `en.ts`, write `vi.ts`, add the `m`
  parameter, and update call sites** (components call `useT()` once and pass it down).
  Matching code in `suggestions.ts` (the name regexes, `folded`) is not text and stays.
- [ ] **Step 4: Run, expect PASS.** Run: `cd web && npm test && npm run typecheck`
- [ ] **Step 5: Commit** `feat(web): explanations, suggestions, and run text in both languages`.

### Task 4: Setup screens

**Files:**
- Modify: `web/src/App.tsx`, `Workspace.tsx`, `UploadForm.tsx`, `SpecEditor.tsx`,
  `DatasetView.tsx`, `components/top-bar.tsx`, `components/theme-toggle.tsx`,
  `components/status-badge.tsx`, `components/data-table.tsx`, `components/dataset-summary.tsx`,
  `components/setup/stepper.tsx`, `components/logo.tsx`
- Modify: `web/src/i18n/en.ts`, `vi.ts` (sections `app`, `setup`, `dataset`, `theme`)
- Test: `web/src/i18n/literals.test.ts`

**Interfaces:**
- Consumes: `useT()`, `datasetStatus(status, m)`, `columnDetail`, `share` (Task 3).
- Produces: dictionary sections only.

- [ ] **Step 1: Remove these files from `PENDING`.**
- [ ] **Step 2: Run, expect FAIL** with the literals listed. Run: `cd web && npm test -- literals`
- [ ] **Step 3: Move each listed string, plus template-literal text and `toLocaleString()` calls
  (use `m.locale`), into the dictionaries.** The literal check misses lowercase and
  interpolated text; read each file top to bottom.
- [ ] **Step 4: Run, expect PASS.** Run: `cd web && npm test && npm run typecheck`
- [ ] **Step 5: Commit** `feat(web): setup screens in both languages`.

### Task 5: Conversation screens, then check the whole UI

**Files:**
- Modify: `web/src/Conversation.tsx`, `VersionReview.tsx`, `Chart.tsx` (UI text only),
  `components/memory-panel.tsx`, `components/chat/run-message.tsx`, `evidence.tsx`,
  `run-feedback.tsx`, `save-metric-dialog.tsx`, `add-files-dialog.tsx`, `version-status.tsx`
- Modify: `web/src/i18n/en.ts`, `vi.ts` (sections `chat`, `evidence`, `memory`, `versionReview`)
- Create: `docs/learning/25-vietnamese-ui.md` (Vietnamese), update `docs/learning/README.md`
- Modify: `docs/progress.md`, spec status line in `docs/design/2026-11-22-vietnamese-ui.md`
- Test: `web/src/i18n/literals.test.ts`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces: `PENDING` is empty.

- [ ] **Step 1: Remove these files from `PENDING`, so it is `[]`.**
- [ ] **Step 2: Run, expect FAIL.** Run: `cd web && npm test -- literals`
- [ ] **Step 3: Translate as in Task 4.** `VersionReview.tsx`'s overlap texts (`exact`, `key`,
  `dates`) become dictionary functions. `run.error` (server text) stays as sent; only its frame
  ("The run failed") is translated.
- [ ] **Step 4: Run all checks, expect PASS.**
  Run: `cd web && npm test && npm run typecheck && npm run build` and `uv run pytest`
- [ ] **Step 5: Check the real UI** with the `run` skill: in Vietnamese, upload a file from
  `examples/`, ask a suggested question, open "How it was calculated", open add files. Take
  screenshots at 1280 px and 375 px. Expected: no English on these paths except data, server text
  for uncoded errors, and the language names; no clipped or overflowing labels; switching back to
  English changes everything without a reload; after a reload the choice is kept. Asking costs
  one real model call per question: use at most two questions.
- [ ] **Step 6: Write note 25** (what i18n is, why typed dictionaries over a library, why codes
  instead of server translation, what stays English), update progress (replace stale lines,
  keep under 60), set the spec status to done.
- [ ] **Step 7: Commit** `feat(web): conversation screens in Vietnamese; docs`.
