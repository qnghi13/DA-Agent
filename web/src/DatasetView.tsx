import { useEffect, useState } from "react";
import { type Api, BUSY_STATUSES, type Dataset, type DatasetSpec, errorMessage } from "./api";
import { SpecEditor } from "./SpecEditor";

const POLL_MS = 2000;

const STATUS_TEXT: Record<Dataset["status"], string> = {
  uploaded: "Reading the files and proposing a spec. This can take a minute.",
  proposed: "Review how the files will be read, then approve or edit the spec.",
  building: "Rebuilding the dataset with your edited spec.",
  ready: "Ready for questions.",
  failed: "The dataset could not be prepared.",
};

export function DatasetView({
  api,
  datasetId,
  onChange,
}: {
  api: Api;
  datasetId: string;
  onChange: (dataset: Dataset) => void;
}) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  // Bumped after an approval so the poll starts again.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function load() {
      try {
        const next = await api.getDataset(datasetId);
        if (cancelled) return;
        setDataset(next);
        onChange(next);
        setError(null);
        if (BUSY_STATUSES.includes(next.status)) timer = window.setTimeout(load, POLL_MS);
      } catch (caught) {
        if (!cancelled) setError(errorMessage(caught));
      }
    }
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, datasetId, onChange, version]);

  async function approve(spec?: DatasetSpec) {
    setApproving(true);
    setError(null);
    try {
      await api.approveDataset(datasetId, spec);
      setVersion((v) => v + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setApproving(false);
    }
  }

  if (!dataset) {
    return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  }

  const summary = dataset.summary;
  return (
    <div className="stack">
      <header>
        <h2>
          {dataset.name} <span className={`badge ${dataset.status}`}>{dataset.status}</span>
        </h2>
        <p className="muted">
          {BUSY_STATUSES.includes(dataset.status) && <span className="spinner" aria-hidden />}
          {STATUS_TEXT[dataset.status]}
        </p>
        {dataset.error && <p className="error">{dataset.error}</p>}
      </header>

      <details>
        <summary>
          {dataset.files.length} file{dataset.files.length === 1 ? "" : "s"}
          {dataset.documents > 0 && `, ${dataset.documents} document(s)`}
        </summary>
        <ul className="file-list">
          {dataset.files.map((file) => (
            <li key={file.stored_as}>
              {file.name}
              {file.stored_as !== file.name && <span className="muted"> → {file.stored_as}</span>}
            </li>
          ))}
        </ul>
        {dataset.description && <p className="prewrap">{dataset.description}</p>}
      </details>

      {summary?.tables && summary.tables.length > 0 && (
        <section>
          <h3>Tables</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Table</th>
                  <th className="num">Rows</th>
                  <th className="num">Columns</th>
                  <th>One row is</th>
                </tr>
              </thead>
              <tbody>
                {summary.tables.map((table) => (
                  <tr key={table.name}>
                    <td>
                      <code>{table.name}</code>
                    </td>
                    <td className="num">{table.rows.toLocaleString()}</td>
                    <td className="num">{table.columns}</td>
                    <td>{table.row}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <Notes title="Problems to check" tone="warn" items={summary?.problems} />
      <Notes title="Proposal problems" tone="warn" items={summary?.proposal_problems} />
      <Notes title="Exclusion rules dropped" tone="info" items={summary?.dropped_rules} />
      <Notes title="Exact repeats removed" tone="info" items={summary?.deduplicated} />

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {dataset.status === "proposed" && dataset.spec && (
        <SpecEditor spec={dataset.spec} busy={approving} onApprove={approve} />
      )}
      {dataset.status === "ready" && dataset.spec && (
        <details>
          <summary>Approved spec</summary>
          <pre className="code">{JSON.stringify(dataset.spec, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function Notes({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "warn" | "info";
  items: string[] | undefined;
}) {
  if (!items?.length) return null;
  return (
    <section className={`notes ${tone}`}>
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
