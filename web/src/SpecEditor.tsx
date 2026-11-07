import { useMemo, useState } from "react";
import { COLUMN_TYPES, type ColumnSpec, type DatasetSpec, type TableSpec } from "./api";

type Mode = "form" | "json";

/**
 * Review a proposed spec. Column names, types, descriptions, and the sensitive flag edit in place;
 * anything else (rules, relationships) edits as JSON. The server validates every edit.
 */
export function SpecEditor({
  spec: proposed,
  busy,
  onApprove,
}: {
  spec: DatasetSpec;
  busy: boolean;
  onApprove: (spec?: DatasetSpec) => void;
}) {
  const [spec, setSpec] = useState<DatasetSpec>(proposed);
  const [mode, setMode] = useState<Mode>("form");
  const [json, setJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

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
      if (!parsed || !Array.isArray(parsed.tables)) throw new Error("the spec needs a tables list");
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
    <section className="stack">
      <div className="spec-header">
        <h3>Spec</h3>
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "form"}
            onClick={() => switchMode("form")}
          >
            Columns
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "json"}
            onClick={() => switchMode("json")}
          >
            JSON
          </button>
        </div>
      </div>

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
            <section className="notes info">
              <h3>Notes from the proposal</h3>
              <ul>
                {spec.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        <>
          <textarea
            className="code"
            rows={24}
            spellCheck={false}
            value={json}
            onChange={(event) => setJson(event.target.value)}
          />
          {jsonError && <p className="error">Invalid JSON: {jsonError}</p>}
        </>
      )}

      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={approve}>
          {busy ? "Sending…" : edited || mode === "json" ? "Save and rebuild" : "Approve"}
        </button>
        {edited && (
          <button
            type="button"
            className="link"
            disabled={busy}
            onClick={() => {
              setSpec(proposed);
              setJson(JSON.stringify(proposed, null, 2));
              setJsonError(null);
            }}
          >
            Discard edits
          </button>
        )}
      </div>
    </section>
  );
}

function TableEditor({ table, onChange }: { table: TableSpec; onChange: (t: TableSpec) => void }) {
  function updateColumn(index: number, patch: Partial<ColumnSpec>) {
    onChange({
      ...table,
      columns: table.columns.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    });
  }

  const rules = table.exclude_rows ?? [];
  return (
    <details className="table-spec" open>
      <summary>
        <code>{table.name}</code>
        {table.source !== table.name && <span className="muted"> from {table.source}</span>}
      </summary>
      <label>
        Description
        <input
          maxLength={500}
          value={table.description ?? ""}
          onChange={(event) => onChange({ ...table, description: event.target.value })}
        />
      </label>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source column</th>
              <th>Name</th>
              <th>Type</th>
              <th>Description</th>
              <th title="Personal data: the column is left out of the built dataset">
                Sensitive
              </th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map((column, index) => (
              <tr key={column.source}>
                <td>
                  <code>{column.source}</code>
                  {column.date_formats?.length ? (
                    <span className="muted"> ({column.date_formats.join(", ")})</span>
                  ) : null}
                </td>
                <td>
                  <input
                    aria-label={`Name of ${column.source}`}
                    value={column.name}
                    onChange={(event) => updateColumn(index, { name: event.target.value })}
                  />
                </td>
                <td>
                  <select
                    aria-label={`Type of ${column.source}`}
                    value={column.type}
                    onChange={(event) =>
                      updateColumn(index, { type: event.target.value as ColumnSpec["type"] })
                    }
                  >
                    {COLUMN_TYPES.map((type) => (
                      <option key={type}>{type}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    aria-label={`Description of ${column.source}`}
                    maxLength={300}
                    value={column.description ?? ""}
                    onChange={(event) => updateColumn(index, { description: event.target.value })}
                  />
                </td>
                <td className="center">
                  <input
                    type="checkbox"
                    aria-label={`${column.source} is sensitive`}
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
        <ul className="muted small">
          {table.fill_down?.length ? <li>Fill down: {table.fill_down.join(", ")}</li> : null}
          {table.dedupe === "exact" && <li>Exact repeated rows are removed.</li>}
          {rules.map((rule, i) => (
            <li key={i}>
              Exclude rows where{" "}
              {rule.when
                .map((c) => `${c.column} ${c.op.replace("_", " ")}${c.value ? ` "${c.value}"` : ""}`)
                .join(" and ")}
              {rule.reason && ` (${rule.reason})`}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
