import { ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { COLUMN_TYPES, type ColumnSpec, type DatasetSpec, type TableSpec } from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";
import { useT } from "./i18n/context";
import { cn } from "./lib/utils";

type Mode = "form" | "json";

/**
 * Review a proposed spec. Column names, types, descriptions, and the sensitive flag edit in place;
 * anything else (rules, relationships) edits as JSON. The server validates every edit.
 */
export function SpecEditor({
  spec: proposed,
  busy,
  onApprove,
  embedded = false,
  onChange,
}: {
  spec: DatasetSpec;
  busy: boolean;
  onApprove: (spec?: DatasetSpec) => void;
  /** Inside another review: columns only, no title, no Approve button; `onChange` gets each edit. */
  embedded?: boolean;
  onChange?: (spec: DatasetSpec) => void;
}) {
  const m = useT();
  const [spec, setSpec] = useState<DatasetSpec>(proposed);
  const [mode, setMode] = useState<Mode>("form");
  const [json, setJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => onChange?.(spec), [spec, onChange]);

  const original = useMemo(() => JSON.stringify(proposed), [proposed]);
  const edited = JSON.stringify(spec) !== original;

  function switchMode(next: Mode) {
    if (next === mode) return;
    if (next === "json") {
      setJson(JSON.stringify(spec, null, 2));
      setJsonError(null);
      setMode("json");
      return;
    }
    if (applyJson()) setMode("form");
  }

  function applyJson(): DatasetSpec | null {
    try {
      const parsed = JSON.parse(json) as DatasetSpec;
      if (!parsed || !Array.isArray(parsed.tables)) throw new Error(m.spec.needsTables);
      setSpec(parsed);
      setJsonError(null);
      return parsed;
    } catch (caught) {
      setJsonError(caught instanceof Error ? caught.message : String(caught));
      return null;
    }
  }

  function approve() {
    const current = mode === "json" ? applyJson() : spec;
    if (!current) return;
    onApprove(JSON.stringify(current) === original ? undefined : current);
  }

  function updateTable(index: number, table: TableSpec) {
    setSpec((s) => ({ ...s, tables: s.tables.map((t, i) => (i === index ? table : t)) }));
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby="spec-title">
      {!embedded && (
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="spec-title" className="text-lg font-semibold tracking-tight">
            {m.spec.title}
          </h2>
          <p className="text-muted-foreground text-sm">{m.spec.intro}</p>
        </div>
        <div className="bg-muted inline-flex rounded-lg border p-0.5" role="tablist" aria-label={m.spec.editAs}>
          {(["form", "json"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              onClick={() => switchMode(value)}
              className={cn(
                "rounded-md px-3 py-1 text-sm transition-colors",
                mode === value
                  ? "bg-background text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {value === "form" ? m.spec.columns : "JSON"}
            </button>
          ))}
        </div>
      </div>
      )}

      {mode === "form" ? (
        <>
          {spec.tables.map((table, index) => (
            <TableEditor
              key={table.source}
              table={table}
              onChange={(next) => updateTable(index, next)}
            />
          ))}
          {spec.notes && spec.notes.length > 0 && (
            <section className="border-info-line bg-info-soft rounded-lg border px-4 py-3">
              <h3 className="text-info mb-1 text-sm font-medium">{m.spec.notes}</h3>
              <ul className="list-disc pl-5 text-sm">
                {spec.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        <>
          <Textarea
            aria-label={m.spec.asJson}
            className="min-h-[28rem] font-mono text-xs leading-relaxed"
            spellCheck={false}
            value={json}
            onChange={(event) => setJson(event.target.value)}
          />
          {jsonError && (
            <p role="alert" className="text-danger text-sm">
              {m.spec.invalidJson(jsonError)}
            </p>
          )}
        </>
      )}

      {!embedded && (
      <div className="bg-background/90 sticky bottom-0 -mx-1 flex items-center justify-end gap-2 border-t px-1 py-4 backdrop-blur">
        {edited && (
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setSpec(proposed);
              setJson(JSON.stringify(proposed, null, 2));
              setJsonError(null);
            }}
          >
            {m.spec.discardEdits}
          </Button>
        )}
        <Button type="button" disabled={busy} onClick={approve}>
          {busy && <Loader2 className="animate-spin" />}
          {busy ? m.spec.sending : edited || mode === "json" ? m.spec.saveAndRebuild : m.spec.approve}
        </Button>
      </div>
      )}
    </section>
  );
}

function TableEditor({ table, onChange }: { table: TableSpec; onChange: (t: TableSpec) => void }) {
  const m = useT();
  function updateColumn(index: number, patch: Partial<ColumnSpec>) {
    onChange({
      ...table,
      columns: table.columns.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    });
  }

  const rules = table.exclude_rows ?? [];
  const field =
    "border-input bg-background focus-visible:border-ring focus-visible:ring-ring/50 h-8 w-full min-w-0 rounded-md border px-2 text-sm outline-none focus-visible:ring-[3px]";
  return (
    <details className="group bg-card overflow-hidden rounded-xl border" open>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3">
        <ChevronRight className="text-muted-foreground size-4 transition-transform group-open:rotate-90" />
        <code className="text-sm font-semibold">{table.name}</code>
        {table.source !== table.name && (
          <span className="text-muted-foreground truncate text-xs">{m.spec.from(table.source)}</span>
        )}
        <span className="text-muted-foreground ml-auto text-xs">
          {m.spec.columnCount(table.columns.length)}
        </span>
      </summary>
      <div className="flex flex-col gap-4 border-t px-4 py-4">
        <div className="grid gap-1.5">
          <Label htmlFor={`table-${table.source}`}>{m.spec.description}</Label>
          <Input
            id={`table-${table.source}`}
            maxLength={500}
            value={table.description ?? ""}
            onChange={(event) => onChange({ ...table, description: event.target.value })}
          />
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead className="bg-muted">
              <tr className="text-muted-foreground text-left text-xs">
                <th className="px-3 py-2 font-medium">{m.spec.sourceColumn}</th>
                <th className="px-2 py-2 font-medium">{m.spec.name}</th>
                <th className="px-2 py-2 font-medium">{m.spec.type}</th>
                <th className="px-2 py-2 font-medium">{m.spec.description}</th>
                <th className="px-3 py-2 text-center font-medium" title={m.spec.sensitiveHint}>
                  {m.spec.sensitive}
                </th>
              </tr>
            </thead>
            <tbody>
              {table.columns.map((column, index) => (
                <tr key={column.source} className="border-t">
                  <td className="px-3 py-1.5 align-middle">
                    <code className="text-[13px]">{column.source}</code>
                    {column.date_formats?.length ? (
                      <span className="text-muted-foreground block text-xs">
                        {column.date_formats.join(", ")}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5">
                    <input
                      className={cn(field, "font-mono text-[13px]")}
                      aria-label={m.spec.nameOf(column.source)}
                      value={column.name}
                      onChange={(event) => updateColumn(index, { name: event.target.value })}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <select
                      className={cn(field, "pr-6")}
                      aria-label={m.spec.typeOf(column.source)}
                      value={column.type}
                      onChange={(event) =>
                        updateColumn(index, { type: event.target.value as ColumnSpec["type"] })
                      }
                    >
                      {COLUMN_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {m.spec.types[type]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-1.5">
                    <input
                      className={field}
                      aria-label={m.spec.descriptionOf(column.source)}
                      maxLength={300}
                      value={column.description ?? ""}
                      onChange={(event) => updateColumn(index, { description: event.target.value })}
                    />
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    <input
                      type="checkbox"
                      className="accent-foreground size-4 align-middle"
                      aria-label={m.spec.isSensitive(column.source)}
                      checked={column.sensitive ?? false}
                      onChange={(event) => updateColumn(index, { sensitive: event.target.checked })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(rules.length > 0 || table.fill_down?.length || table.dedupe === "exact") && (
          <ul className="text-muted-foreground flex flex-col gap-1 text-sm">
            {table.fill_down?.length ? <li>{m.spec.fillDown(table.fill_down.join(", "))}</li> : null}
            {table.dedupe === "exact" && <li>{m.spec.dedupe}</li>}
            {rules.map((rule, i) => (
              <li key={i}>
                {m.spec.excludeWhere(
                  rule.when
                    .map((c) => {
                      const op = m.spec.ruleOps[c.op] ?? c.op.replace("_", " ");
                      return `${c.column} ${op}${c.value ? ` “${c.value}”` : ""}`;
                    })
                    .join(m.explain.and),
                )}
                {rule.reason && ` (${rule.reason})`}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}
