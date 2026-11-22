// Pure logic for adding files to a dataset (docs/design/2026-11-21-add-files.md).

import type { DatasetVersion, OverlapChoice, VersionChange, VersionOverlap } from "./api";

const OPEN: readonly DatasetVersion["status"][] = [
  "uploaded",
  "checking",
  "needs_choice",
  "proposed",
  "building",
];
const WAITING: readonly DatasetVersion["status"][] = ["needs_choice", "proposed"];

/** Still being checked, waiting for the user, or building: the UI polls while one exists. */
export function isOpenVersion(version: Pick<DatasetVersion, "status">): boolean {
  return OPEN.includes(version.status);
}

export function isWaitingForUser(version: Pick<DatasetVersion, "status">): boolean {
  return WAITING.includes(version.status);
}

/** Waiting for the user, or left in a step by a job that ended: the user can drop it. */
export function canDiscard(version: Pick<DatasetVersion, "status" | "stuck">): boolean {
  return isWaitingForUser(version) || (isOpenVersion(version) && version.stuck === true);
}

/** The run id before which a divider shows, and the version that starts there. */
export function versionDividers(
  runs: { id: string; dataset_version?: number }[],
): Map<string, number> {
  const dividers = new Map<string, number>();
  let previous: number | null = null;
  for (const run of runs) {
    const version = run.dataset_version ?? 1;
    if (previous !== null && version > previous) dividers.set(run.id, version);
    previous = version;
  }
  return dividers;
}

/** An answer from an earlier data version than the one questions run on now. */
export function isOlderVersion(run: { dataset_version?: number }, current: number): boolean {
  return (run.dataset_version ?? 1) < current;
}

/** Names of the files a version has that the version before it did not. */
export function addedFiles(versions: DatasetVersion[], version: number): string[] {
  const sorted = [...versions].sort((a, b) => a.version - b.version);
  const found = sorted.find((v) => v.version === version);
  if (!found) return [];
  const before = [...sorted].reverse().find((v) => v.version < version);
  const known = new Set((before?.files ?? []).map((f) => f.stored_as));
  return found.files.filter((f) => !known.has(f.stored_as)).map((f) => f.name);
}

/** Share of filled cells that parsed, over one table's columns; null when the summary lacks it. */
function parseRate(version: DatasetVersion, table: string): number | null {
  const profile = version.summary?.tables?.find((t) => t.name === table)?.profile;
  if (!profile) return null;
  const filled = profile.reduce((sum, c) => sum + c.filled, 0);
  if (filled === 0) return null;
  return (profile.reduce((sum, c) => sum + c.parsed, 0) / filled) * 100;
}

/** One line for the status card. */
export function statusLine(version: DatasetVersion): string {
  if (version.stuck && isOpenVersion(version)) {
    return "Checking the new files stopped before it finished. Discard them and add them again.";
  }
  switch (version.status) {
    case "uploaded":
    case "checking":
      return "Checking new files…";
    case "needs_choice":
    case "proposed":
      return "Your new files need a decision";
    case "building":
      return "Adding the new files…";
    case "failed":
      return `Couldn't add files: ${version.error ?? "no details were recorded"}`;
    case "discarded":
      return "Discarded the new files";
    case "applied": {
      const grown = (version.changes ?? []).filter((c) => c.new_rows > 0);
      const rows = grown.reduce((sum, c) => sum + c.new_rows, 0);
      const table = grown.length === 1 ? grown[0].table : `${grown.length} tables`;
      const rate = grown.length === 1 ? parseRate(version, grown[0].table) : null;
      const tail = rate === null ? "" : ` (parse rate ${rate.toFixed(1)}%)`;
      return `Added ${rows.toLocaleString("en-US")} rows to ${table}${tail}`;
    }
  }
}

/** Tables that have an overlap the user must decide on. */
export function decisionTables(changes: VersionChange[]): VersionChange[] {
  return changes.filter(
    (c) => c.kind !== "removed" && c.overlaps.some((o) => !o.removed_by_dedupe),
  );
}

/** Tables still lacking a choice, or a file to replace when the choice is replace. */
export function missingDecisions(
  changes: VersionChange[],
  chosen: Record<string, OverlapChoice | undefined>,
  replaceFiles: Record<string, string | undefined>,
): string[] {
  return decisionTables(changes)
    .filter((c) => !chosen[c.table] || (chosen[c.table] === "replace" && !replaceFiles[c.table]))
    .map((c) => c.table);
}

/** A note when overlapping rows are also in another file of the same upload, or null. */
export function earlierNewFilesText(overlap: Pick<VersionOverlap, "earlier_new_files">): string | null {
  const files = overlap.earlier_new_files ?? [];
  if (files.length === 0) return null;
  return `Some of these rows are also in ${files.join(", ")}, sent in this same upload.`;
}
