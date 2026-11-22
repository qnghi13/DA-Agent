import { Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { type Api, type Dataset, type DatasetSpec, type DatasetVersion, type OverlapChoice, type RepairChange, type VersionChange } from "./api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { Button } from "./components/ui/button";
import { Label } from "./components/ui/label";
import { SpecEditor } from "./SpecEditor";
import {
  decisionTables,
  earlierNewFilesText,
  missingDecisions,
  replaceDefaults,
  replaceOptions,
} from "./versions";

const CHOICE_LABELS: Record<OverlapChoice, string> = {
  replace: "Replace old file",
  drop_overlap: "Drop overlapping rows",
  keep_both: "Keep both",
};

const KIND_TEXT: Record<"exact" | "key" | "date", (rows: number, key?: string[], column?: string | null) => string> = {
  exact: (rows) => `${rows.toLocaleString("en-US")} new rows repeat older rows exactly.`,
  key: (rows, key) =>
    `${rows.toLocaleString("en-US")} new rows have the same ${key?.join(", ") || "key"} as older rows.`,
  date: (rows, _key, column) =>
    `${rows.toLocaleString("en-US")} new rows fall inside the dates the old data already covers${column ? ` (${column})` : ""}.`,
};

const show = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/**
 * Review of a dataset version that waits for the user: overlaps, new tables, proposed repairs, and
 * what goes away. Sends the decisions to the build step (docs/design/2026-11-21-add-files.md).
 */
export function VersionReview({
  api,
  dataset,
  version,
  onDone,
}: {
  api: Api;
  dataset: Dataset;
  version: DatasetVersion;
  /** Called after the decisions are sent or the version is discarded. */
  onDone: () => void;
}) {
  const m = useT();
  const changes = version.changes ?? [];
  const repairs = version.repairs ?? [];
  const newNames = useMemo(
    () => new Set(changes.filter((c) => c.kind === "new").map((c) => c.table)),
    [changes],
  );
  // Only new tables are editable; the others keep the spec the dataset already has.
  const proposed = useMemo<DatasetSpec | null>(
    () =>
      version.spec ? { ...version.spec, tables: version.spec.tables.filter((t) => newNames.has(t.name)) } : null,
    [version.spec, newNames],
  );
  const [edited, setEdited] = useState<DatasetSpec | null>(null);
  const [chosen, setChosen] = useState<Record<string, OverlapChoice | undefined>>({});
  // A table with one old file has nothing to pick.
  const [replaceFiles, setReplaceFiles] = useState<Record<string, string | undefined>>(() =>
    replaceDefaults(changes, dataset.files),
  );
  const [accepted, setAccepted] = useState<boolean[]>(() => repairs.map(() => true));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missing = missingDecisions(changes, chosen, replaceFiles);
  const repairProblem = version.summary?.repair_problem;
  const removed = changes.filter((c) => c.kind === "removed");
  const near = changes.filter((c) => c.kind === "new" && c.near_match);

  async function send(applyAnyway: boolean) {
    setBusy(true);
    setError(null);
    const choices: Record<string, OverlapChoice> = {};
    for (const [table, choice] of Object.entries(chosen)) if (choice) choices[table] = choice;
    const files: Record<string, string> = {};
    for (const [table, choice] of Object.entries(choices)) {
      const file = replaceFiles[table];
      if (choice === "replace" && file) files[table] = file;
    }
    const changed = edited && proposed && JSON.stringify(edited) !== JSON.stringify(proposed);
    try {
      await api.approveVersion(dataset.id, version.version, {
        choices,
        replace_files: files,
        spec: changed ? edited : null,
        repairs: repairs.filter((_, i) => accepted[i]),
        apply_anyway: applyAnyway,
      });
      onDone();
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    setError(null);
    try {
      await api.discardVersion(dataset.id, version.version);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-4">
        {decisionTables(changes).map((change) => (
          <OverlapSection
            key={change.table}
            change={change}
            choice={chosen[change.table]}
            file={replaceFiles[change.table]}
            files={replaceOptions(change, dataset.files)}
            onChoice={(choice) => setChosen((c) => ({ ...c, [change.table]: choice }))}
            onFile={(file) => setReplaceFiles((f) => ({ ...f, [change.table]: file }))}
          />
        ))}

        {near.map((change) => (
          <p key={change.table} className="border-warning-line bg-warning-soft rounded-lg border px-3 py-2 text-sm">
            Looks like {change.near_match}, but columns differ: +{change.extra_columns.length} −
            {change.missing_columns.length}. It will be a separate table.
            {(change.extra_columns.length > 0 || change.missing_columns.length > 0) && (
              <span className="text-muted-foreground block text-xs">
                {change.extra_columns.length > 0 && <>New columns: {change.extra_columns.join(", ")}. </>}
                {change.missing_columns.length > 0 && <>Missing columns: {change.missing_columns.join(", ")}.</>}
              </span>
            )}
          </p>
        ))}

        {proposed && proposed.tables.length > 0 && (
          <section className="flex flex-col gap-2" aria-labelledby="new-tables">
            <h3 id="new-tables" className="text-sm font-semibold">
              New tables
            </h3>
            <p className="text-muted-foreground text-xs">
              How the new files become tables. Edit names, types, and descriptions before they are added.
            </p>
            <SpecEditor spec={proposed} busy={busy} onApprove={() => {}} embedded onChange={setEdited} />
          </section>
        )}

        {repairs.length > 0 && (
          <section className="flex flex-col gap-2" aria-labelledby="repairs">
            <h3 id="repairs" className="text-sm font-semibold">
              Proposed fixes to existing tables
            </h3>
            <p className="text-muted-foreground text-xs">
              Some values in the new files did not parse. These changes to how the table is read may fix that.
            </p>
            <ul className="divide-y rounded-lg border">
              {repairs.map((repair, index) => (
                <RepairRow
                  key={`${repair.table}:${repair.path}`}
                  repair={repair}
                  checked={accepted[index] ?? false}
                  onChange={(on) => setAccepted((a) => a.map((v, i) => (i === index ? on : v)))}
                />
              ))}
            </ul>
          </section>
        )}

        {changes.some((c) => c.quality.length > 0) && (
          <section className="flex flex-col gap-1" aria-labelledby="quality">
            <h3 id="quality" className="text-sm font-semibold">
              Parse problems
            </h3>
            <ul className="list-disc pl-5 text-sm">
              {changes.flatMap((c) => c.quality.map((q) => <li key={`${c.table}:${q}`}>{q}</li>))}
            </ul>
          </section>
        )}
        {repairProblem && (
          <p role="alert" className="border-warning-line bg-warning-soft rounded-lg border px-3 py-2 text-sm">
            The agent could not propose a fix: {repairProblem}
          </p>
        )}

        {removed.length > 0 && (
          <section className="flex flex-col gap-1" aria-labelledby="removed">
            <h3 id="removed" className="text-sm font-semibold">
              Tables that go away
            </h3>
            <ul className="list-disc pl-5 text-sm">
              {removed.map((c) => (
                <li key={c.table}>
                  <code>{c.table}</code> is not in the new files.
                  {c.stale_metrics.length > 0 && (
                    <span className="text-warning block">
                      Saved definitions that stop working: {c.stale_metrics.join(", ")}.
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <div className="bg-background flex flex-col gap-2 border-t px-4 py-3">
        {error && (
          <p role="alert" className="text-danger text-sm">
            {error}
          </p>
        )}
        {missing.length > 0 && (
          <p className="text-muted-foreground text-xs">Choose what to do for: {missing.join(", ")}.</p>
        )}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="ghost" disabled={busy} onClick={discard}>
            Discard
          </Button>
          {repairProblem && (
            <Button type="button" variant="outline" disabled={busy || missing.length > 0} onClick={() => send(true)}>
              Apply anyway
            </Button>
          )}
          <Button type="button" disabled={busy || missing.length > 0} onClick={() => send(false)}>
            {busy && <Loader2 className="animate-spin" />}
            Apply
          </Button>
        </div>
      </div>
    </div>
  );
}

function OverlapSection({
  change,
  choice,
  file,
  files,
  onChoice,
  onFile,
}: {
  change: VersionChange;
  choice: OverlapChoice | undefined;
  file: string | undefined;
  files: Dataset["files"];
  onChoice: (choice: OverlapChoice) => void;
  onFile: (stored: string) => void;
}) {
  const overlaps = change.overlaps.filter((o) => !o.removed_by_dedupe);
  const allowed = change.allowed_choices ?? ["replace", "keep_both"];
  const id = `overlap-${change.table}`;
  return (
    <fieldset className="bg-card flex flex-col gap-3 rounded-xl border p-4">
      <legend className="px-1 text-sm font-semibold">
        <code>{change.table}</code>: new rows overlap the old data
      </legend>
      {overlaps.map((overlap, i) => (
        <div key={i} className="flex flex-col gap-1 text-sm">
          <p>{KIND_TEXT[overlap.kind](overlap.rows, overlap.key, overlap.column)}</p>
          {earlierNewFilesText(overlap) && (
            <p className="text-muted-foreground">{earlierNewFilesText(overlap)}</p>
          )}
          {overlap.examples && overlap.examples.length > 0 && (
            <ul className="text-muted-foreground bg-muted flex flex-col gap-0.5 rounded-md px-3 py-2 font-mono text-xs">
              {overlap.examples.slice(0, 3).map((example, j) => (
                <li key={j} className="break-words">
                  {Object.entries(example)
                    .map(([k, v]) => `${k}=${v ?? "empty"}`)
                    .join(", ")}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      <div role="radiogroup" aria-label={`What to do with the overlap in ${change.table}`} className="flex flex-col gap-1.5">
        {allowed.map((value) => (
          <label key={value} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={id}
              value={value}
              checked={choice === value}
              onChange={() => onChoice(value)}
            />
            {CHOICE_LABELS[value]}
          </label>
        ))}
      </div>
      {choice === "replace" && (
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-file`}>Old file to replace</Label>
          <select
            id={`${id}-file`}
            value={file ?? ""}
            onChange={(event) => onFile(event.target.value)}
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          >
            <option value="" disabled>
              Choose a file
            </option>
            {files.map((f) => (
              <option key={f.stored_as} value={f.stored_as}>
                {f.name}
              </option>
            ))}
          </select>
        </div>
      )}
    </fieldset>
  );
}

function RepairRow({
  repair,
  checked,
  onChange,
}: {
  repair: RepairChange;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  const id = `repair-${repair.table}-${repair.path}`;
  return (
    <li className="flex items-start gap-3 px-3 py-2 text-sm">
      <input
        id={id}
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <label htmlFor={id} className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span>
          Accept the change to <code>{repair.table}</code> · <code>{repair.path}</code>
        </span>
        <span className="text-muted-foreground font-mono text-xs break-words">
          before: {show(repair.before)}
        </span>
        <span className="font-mono text-xs break-words">after: {show(repair.after)}</span>
      </label>
    </li>
  );
}
