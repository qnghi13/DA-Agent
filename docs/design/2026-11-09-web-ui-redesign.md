# Web UI redesign: Vercel style, chat-first layout

**Status:** approved 2026-11-09. **Date:** 2026-11-09. **Scope:** `web/` only.

## Intent

The backend is complete (plan Days 1–21). The web UI works but its layout is weak and its
components look plain. The user wants it to look and feel like a modern AI product, specifically
in the style of Vercel: monochrome, thin borders, crisp type, quiet chrome. It is a portfolio
piece and will appear in the demo video, so first impressions matter, but it must also stay
pleasant to use for repeated questions.

## Goals

- A chat-first layout (decided): dataset list in a left sidebar, the conversation in the middle,
  the question box pinned at the bottom.
- Answers that show live progress while running, then collapse so the chat stays readable, with
  the evidence (steps, SQL, data, report, trace) one click away in a right-hand panel.
- A clear, guided flow to create a dataset: Upload → Review spec → Ready.
- Consistent, accessible components in light and dark themes, working from phone (390 px) to
  desktop (1440 px).

## Non-goals

- No API, backend, or data-model changes. Every screen uses the endpoints `web/src/api.ts`
  already calls.
- No new features. Exceptions that are presentation only: a light/dark toggle, and collapsing a
  finished run's steps into a summary line.
- Not a performance project. The Vega chunk (853 kB) stays lazy-loaded as today; shrinking it is
  out of scope.

## Anti-goals (a failure even if it works)

- Losing any current capability: upload with description, spec editing and approval, as-of date
  per question, clarification questions, abstentions and failure reasons, earlier-question
  paging, charts, the sandboxed report viewer and download, trace links, invite links, sign-out,
  key sign-in, link sign-in, and every `/ops` section.
- Hiding that an answer is verified or traceable. Traceability is the product's point; the
  redesign must make it more visible, not less.
- Content that jumps: the panel never opens by itself, and finishing a run never removes content
  the user was reading.
- A generic template look: default shadcn with no care for spacing, hierarchy, and copy.

## Constraints

- Stack stays React 19 + Vite 8 + TypeScript. Vitest tests for logic modules (`auth.ts`, `ops.ts`,
  `run.ts`, `sse.ts`, `api.ts`) must keep passing unchanged.
- CI runs `npm ci`, `npm test`, `npm run build`, and `npm audit --omit=dev --audit-level=high`.
  New dependencies must pass the audit. Pin exact versions, as `package.json` does today.
- Model output is untrusted. Never render it as HTML (threat model). Reports stay in the
  sandboxed `iframe` (`sandbox="allow-scripts"`, no `allow-same-origin`).
- nginx serves the build with a CSP that has no `style-src` or `script-src` rule today; do not
  add anything that needs a new CSP exception (no remote fonts or scripts at runtime).
- Charts keep using `vega-interpreter` (no `eval`).

## Approaches considered

1. **Tailwind CSS v4 + shadcn/ui + Geist + Lucide (chosen).** shadcn copies component source into
   the repo, built on Radix primitives, so focus, keyboard, and ARIA come built in, and its
   default look is Vercel's design language. Cost: about eight UI-only dependencies, and the
   ~1,100 lines of hand-written CSS are replaced. Chosen by the user because the goal is "looks
   like Vercel, components look good", and this is that stack.
2. **Keep hand-written CSS with Vercel-style tokens and Geist.** No new libraries, but the slide-in
   panel (focus trap, Escape, full screen on phones), dialogs, and toasts would be hand-built, at
   more effort for a weaker result.
3. **Radix primitives with hand-written CSS.** Accessible primitives without Tailwind, but all
   styling and the Vercel feel would still be rebuilt by hand.

The dependency choice gets an ADR (0027) when implemented, as the project records decisions.

## Decided design

### Visual language

- Monochrome. Light: white surfaces, `#fafafa` subtle backgrounds, `#ebebeb` borders, near-black
  ink. Dark: near-black surfaces with the same structure. Colour only for status: green for
  verified/ready, blue for running, amber for needs review, red for failed.
- Font: Geist Sans for UI, Geist Mono for SQL, ids, and numbers in tables. Bundled locally
  (npm package), replacing IBM Plex.
- Small radii (6–12 px), 1 px borders, very light shadows only on floating elements (composer,
  panel, menus).
- Theme follows the system by default, with a toggle in the top bar that remembers the choice in
  `localStorage`.
- Charts move to the same palette: ink for marks, greys for axes and grid, one accent at most.
  Use the `dataviz` skill when restyling `Chart.tsx` and the `/ops` charts.

### Layout (option B)

```
┌ top bar: logo · current dataset name + status ·········· theme · invite · account ┐
├ sidebar ───────────┬ conversation ─────────────────────────┬ (evidence panel) ─────┤
│ + New dataset      │  user question (right-aligned bubble) │  opened on demand     │
│ Demo shop  Ready   │  answer message                       │                       │
│ Clinic     Review  │  ...                                  │                       │
│                    │                                       │                       │
│                    │  [ composer: question · as-of · ↑ ]   │                       │
└────────────────────┴───────────────────────────────────────┴───────────────────────┘
```

- Sidebar: "+ New dataset", then datasets with a status badge. Collapsible on desktop; a drawer
  behind a menu button on phones. Operators have no workspace and keep their single view: the
  same top bar over the operations dashboard, with no sidebar.
- Conversation column has a readable max width (about 720 px) and scrolls; the composer stays
  pinned at the bottom.
- Composer: a growing textarea (Enter sends, Shift+Enter new line, as today), a compact as-of
  date control, and a round send button. Disabled with a reason when the dataset is not ready.
- Empty conversation: a short prompt and three example questions as clickable chips that fill
  the composer. The examples are phrased for any dataset ("What are the totals by month?"),
  since the UI does not know the domain (my call).

### Answer message

While running (live over SSE, as today):

```
● Running · 4 s
│ ✓ Planned: top products by quantity, May 2026
│ ◉ Running query 2
```

When finished:

```
✓ Verified · 3 queries
8,714,000 ₫                       ← the answer, large
Revenue excludes cancelled orders. ← the agent's reason, if any
▂▃▃▅▄▇                            ← chart, if any
Show work →   Copy
```

- The live steps collapse into the one-line summary when the run ends; nothing else moves.
- The summary uses only what the API returns: "Verified" when `stop_reason` is `answered`
  (the graph answers only after verify passes), and the count of `result.queries`. Runs carry no
  duration or cost for members, so the finished summary shows neither (my call, to keep the API
  unchanged). While running, the elapsed time is a client-side timer.
- Answers that are lists or tables render as a list or a compact table in the message.
- Clarification: the agent's question appears as a message, answered with the composer.
- Abstained or failed: the reason shows in the message, in plain words, with "Show work →".
- "Show work →" opens the evidence panel. It never opens by itself.

### Evidence panel

- A right-hand sheet (shadcn `Sheet`) with the question as its title and tabs: **Steps** (plan
  and each step), **SQL** (each query, monospace, copy button), **Data** (the rows each query
  returned, as tables), **Report** (the sandboxed report frame, plus Download). Trace links and
  the run id sit in the panel footer.
- Desktop: the panel sits beside the conversation and narrows it; it does not cover it. Phone:
  full screen. Escape and a close button close it; focus returns to the "Show work" button.
- Opening another answer's "Show work" switches the panel to that run.

### Create a dataset

A dedicated screen, not part of the chat, with a three-step header: **① Upload → ② Review spec →
③ Ready**.

1. Upload: name, description, and a drag-and-drop file area (CSV, Excel, SQLite) with the file
   list and the limits shown. Submitting moves to step 2.
2. Review spec: while ingestion and the proposal run, a progress state (skeleton and status
   text, as `DatasetView` polls today). Then the spec editor: tables as cards, columns as rows
   (name, type, description, hidden), the proposal's notes, the form/JSON toggle, and the
   current actions: Approve, or Save and rebuild after edits, and Discard edits.
3. Ready: brief confirmation, then the dataset's conversation opens.

Datasets not yet ready open on this screen at their current step when chosen in the sidebar.
Failed ingestion shows the error and the validation report text on step 2.

### Sign-in

A centred card in the Vercel login style: logo, one line explaining sign-in links and keys, the
key field, and the notices the current page shows (spent link, ended session). Signing in with a
link stays automatic.

### Operations (`/ops`)

Same data and sections as `OpsDashboard.tsx` today, restyled as a Vercel-style dashboard: a row
of stat cards per section, budget meters, tables for top workspaces and recent failures, charts
in the new palette.

## Left to the builder

- Component breakdown, file layout under `web/src/`, and which shadcn components to add.
- Tailwind theme tokens and exact greys, within the visual language above.
- How to keep or replace the current component tests; logic-module tests stay as they are.
- Animation details (keep them short and subtle; respect `prefers-reduced-motion`).
- Verification: screenshots with `playwright-cli` at 1440 and 390 px, light and dark, of sign-in,
  upload, review, conversation (running and finished), the evidence panel, and `/ops`, against a
  local stack with the demo data.
