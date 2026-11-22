import { describe, expect, it } from "vitest";

/** No English left in a screen: words a user reads come from the dictionaries in this folder
 * (docs/design/2026-11-22-vietnamese-ui.md). A heuristic: it finds capitalised text, so read each
 * file too; lowercase and interpolated text slip past it. */

const SOURCES = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// Screens not translated yet. Tasks 4 and 5 of the plan empty this list.
const PENDING: string[] = [
  "Chart.tsx",
  "Conversation.tsx",
  "VersionReview.tsx",
  "components/chat/add-files-dialog.tsx",
  "components/chat/evidence.tsx",
  "components/chat/run-feedback.tsx",
  "components/chat/run-message.tsx",
  "components/chat/save-metric-dialog.tsx",
  "components/chat/version-status.tsx",
  "components/memory-panel.tsx",
];

// Never translated: generated UI primitives, and the operator screen (stays English).
const SKIPPED = [/^components\/ui\//, /^OpsDashboard\.tsx$/, /\.test\.tsx$/];

// Capitalised strings that are not words for a user: keys, headers, and names.
const ALLOWED = new Set(["Enter", "Escape", "Content-Type", "English", "Tiếng Việt", "DA-Agent"]);

const PATTERNS: { name: string; regex: RegExp }[] = [
  // Text between JSX tags, such as <span>Sign out</span>; not `=>` or `->`.
  { name: "JSX text", regex: /(?<![=-])>\s*([A-Z][a-z]+[^<>{}]*?)\s*(?=[<{])/g },
  // Attributes a user reads or hears.
  { name: "attribute", regex: /\b(?:aria-label|placeholder|title|alt)="([A-Z][^"]*)"/g },
  // String literals that read as words: "Copy link", "Copied".
  { name: "string", regex: /"([A-Z][a-z]+(?:[ -][^"\n]*|[.…!?:])?)"/g },
  // Template literals that start with a word: `Added ${n} rows`.
  { name: "template", regex: /`([A-Z][a-z]+ [^`\n]*)`/g },
];

function relative(path: string): string {
  return path.replace(/^\.\.\//, "");
}

function findings(path: string, source: string): string[] {
  const found: string[] = [];
  for (const { name, regex } of PATTERNS) {
    for (const match of source.matchAll(regex)) {
      const text = match[1].trim();
      if (ALLOWED.has(text)) continue;
      const line = source.slice(0, match.index).split("\n").length;
      found.push(`${path}:${line} ${name}: ${text}`);
    }
  }
  return found;
}

describe("screens have no hard-coded English", () => {
  const files = Object.entries(SOURCES)
    .map(([path, source]) => [relative(path), source] as const)
    .filter(([path]) => !SKIPPED.some((skip) => skip.test(path)));

  it("checks the files it should", () => {
    expect(files.length).toBeGreaterThan(10);
    const paths = files.map(([path]) => path);
    for (const pending of PENDING) expect(paths).toContain(pending);
  });

  it("finds words only in the dictionaries", () => {
    const found = files
      .filter(([path]) => !PENDING.includes(path))
      .flatMap(([path, source]) => findings(path, source));
    expect(found).toEqual([]);
  });
});
