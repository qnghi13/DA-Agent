# Web UI redesign: implementation plan

Builds [the approved design](2026-11-09-web-ui-redesign.md). Each step ends with `npm test`,
`npm run build`, and screenshots (`playwright-cli`, 1440 and 390 px, light and dark) of the
screens it touched, against the Compose stack with the demo data. The app stays usable after
every step.

New pure logic (summary line, theme choice, step state) starts with a failing Vitest test.
Existing logic modules and their tests (`api.ts`, `auth.ts`, `ops.ts`, `run.ts`, `sse.ts`) keep
their behaviour.

| Step | Work | Done when |
|---|---|---|
| 1. Foundation | ADR 0027. Tailwind v4 (`@tailwindcss/vite`), shadcn/ui init (`@/` alias, `components.json`), Geist Sans and Mono from npm, Lucide. Theme tokens for light and dark, theme toggle with `localStorage` (guarded). Old `styles.css` stays until step 7 | Build passes, audit clean, versions pinned exactly, theme test green |
| 2. Shell and sign-in | Top bar (logo, current dataset and status, theme, invite, account menu with sign-out), sidebar with datasets and status badges, collapsible on desktop, drawer on phones. Sign-in card with the current notices. Operator view: top bar over `/ops`, no sidebar | Link sign-in, key sign-in, spent link, invite, sign-out all work at both widths |
| 3. Conversation | Message list (user bubbles right), earlier-question paging, empty state with example chips, pinned composer (Enter / Shift+Enter, as-of date, send, disabled reason), scroll behaviour as today | Ask a question on the demo data end to end |
| 4. Answer message | Live trail while running with a client-side elapsed timer; collapse to the summary line on finish; answer as scalar, list, or table; reason; chart; clarification, abstention, and failure messages; Copy | Running and finished states, plus a forced abstention, look right |
| 5. Evidence panel | shadcn `Sheet` with Steps, SQL (copy), Data (query tables, truncation note), Report (sandboxed frame, Download); trace links and run id in the footer. Beside the chat on desktop, full screen on phones; Escape closes, focus returns; switching runs | Panel works by keyboard; report frame still sandboxed |
| 6. Create dataset | Stepper Upload → Review spec → Ready; drag-and-drop upload with file list and limits; progress state while ingesting; restyled spec editor (table cards, column rows, notes, form/JSON, Approve / Save and rebuild / Discard edits); ready hand-off to the chat; failed state with the report text. Not-ready datasets open here at their step | Demo upload to approval to first answer in one flow |
| 7. Operations and cleanup | `/ops` as stat cards, meters, tables, charts in the new palette (`dataviz` skill), also `Chart.tsx`. Delete `styles.css` and IBM Plex. Reduced motion, focus rings, contrast check | Every `/ops` section present; no old CSS left |
| 8. Finish | Full screenshot pass, `deploy/smoke.sh` on a fresh stack, README screenshots, learning note 10, progress | User reviews screenshots |
