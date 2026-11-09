# 0027: Tailwind CSS and shadcn/ui for the web UI

**Status:** Accepted, 2026-11-09

**Context:** The UI worked, but its layout was weak and its components plain. The redesign
([design](../design/2026-11-09-web-ui-redesign.md)) aims for a Vercel-style, chat-first interface
with a slide-in evidence panel, menus, tabs, and tooltips that must work by keyboard and on
phones. The UI so far was React with about 1,100 lines of hand-written CSS and no component
library.

**Decision:**
- **Styling:** Tailwind CSS v4 through `@tailwindcss/vite`. Design tokens are CSS variables in
  `web/src/index.css`, light by default and dark under a `.dark` class on `<html>`. Colour marks
  status only.
- **Components:** shadcn/ui. Its source is copied into `web/src/components/ui/` and owned by the
  repo, built on the `radix-ui` primitives (focus management, Escape, ARIA). The `cn` helper is
  our own (`clsx` + `tailwind-merge`); the separate `cn` package the CLI adds is not used.
- **Type and icons:** Geist Sans and Geist Mono from Fontsource (bundled, no runtime requests),
  Lucide icons.
- **Theme:** follows the system until the viewer picks light or dark; the choice sits in
  `localStorage` and is ignored when storage is blocked.
- **Versions:** pinned exactly, each released at least two weeks before it was added. Build-only
  packages (`tailwindcss`, `@tailwindcss/vite`, `tw-animate-css`) are dev dependencies.

**Consequences:** About eight UI dependencies, all front end; the API and backend are unchanged.
The old stylesheet is removed screen by screen. No CSP change: fonts, scripts, and styles are
served from the same origin. Copied shadcn source must be updated by hand.

**Rejected:** hand-written CSS with Vercel-style tokens (more work for the panel's focus and
keyboard handling, and a weaker result); Radix primitives with hand-written CSS (accessible, but
all of the style still rebuilt by hand).
