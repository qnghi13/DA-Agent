// Pure logic for adding files to a dataset (docs/design/2026-11-21-add-files.md).

import type { Dataset, DatasetVersion, OverlapChoice, VersionChange, VersionOverlap } from "./api";
import type { Messages } from "./i18n/en";

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

/** The run id before which a divider shows, with the versions it spans: the earlier run's and
 * the version that starts there. */
export function versionDividers(
  runs: { id: string; dataset_version?: number }[],
): Map<string, { from: number; to: number }> {
  const dividers = new Map<string, { from: number; to: number }>();
  let previous: number | null = null;
  for (const run of runs) {
    const version = run.dataset_version ?? 1;
    if (previous !== null && version > previous) dividers.set(run.id, { from: previous, to: version });
    previous = version;
  }
  return dividers;
}

/** An answer from an earlier data version than the one questions run on now. */
export function isOlderVersion(run: { dataset_version?: number }, current: number): boolean {
  return (run.dataset_version ?? 1) < current;
}

/** Names of the files added after version `from` up to and including version `to`: those the
 * newest applied version up to `to` has and the one at `from` did not. Failed and discarded
 * versions add nothing. */
export function addedFiles(versions: DatasetVersion[], from: number, to: number): string[] {
  const applied = versions.filter((v) => v.status === "applied").sort((a, b) => a.version - b.version);
  const start = [...applied].reverse().find((v) => v.version <= from);
  const end = [...applied].reverse().find((v) => v.version <= to);
  if (!end || end.version <= (start?.version ?? 0)) return [];
  const known = new Set((start?.files ?? []).map((f) => f.stored_as));
  return end.files.filter((f) => !known.has(f.stored_as)).map((f) => f.name);
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
export function statusLine(version: DatasetVersion, m: Messages): string {
  const words = m.versions;
  if (version.stuck && isOpenVersion(version)) return words.stuck;
  switch (version.status) {
    case "uploaded":
    case "checking":
      return words.checking;
    case "needs_choice":
    case "proposed":
      return words.needsDecision;
    case "building":
      return words.adding;
    case "failed":
      return words.failed(version.error ?? null);
    case "discarded":
      return words.discarded;
    case "applied": {
      const grown = (version.changes ?? []).filter((c) => c.new_rows > 0);
      const rows = grown.reduce((sum, c) => sum + c.new_rows, 0);
      const table = grown.length === 1 ? grown[0].table : words.tables(grown.length);
      const rate = grown.length === 1 ? parseRate(version, grown[0].table) : null;
      const tenths = new Intl.NumberFormat(m.locale, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
      const count = new Intl.NumberFormat(m.locale).format(rows);
      return words.added(count, table, rate === null ? null : tenths.format(rate));
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
export function earlierNewFilesText(
  overlap: Pick<VersionOverlap, "earlier_new_files">,
  m: Messages,
): string | null {
  const files = overlap.earlier_new_files ?? [];
  if (files.length === 0) return null;
  return m.versions.earlierNewFiles(files.join(", "));
}

/** The dataset's files a replace choice on this table may name: the table's own. */
export function replaceOptions(change: VersionChange, files: Dataset["files"]): Dataset["files"] {
  const own = new Set(change.files ?? []);
  return files.filter((f) => own.has(f.stored_as));
}

/** The file to replace for each table that has only one: nothing to pick there. */
export function replaceDefaults(changes: VersionChange[], files: Dataset["files"]): Record<string, string> {
  const picked: Record<string, string> = {};
  for (const change of decisionTables(changes)) {
    const options = replaceOptions(change, files);
    if (options.length === 1) picked[change.table] = options[0].stored_as;
  }
  return picked;
}
