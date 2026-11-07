import { useCallback, useEffect, useState } from "react";
import { type Api, type Dataset, errorMessage } from "./api";
import { DatasetView } from "./DatasetView";
import { UploadForm } from "./UploadForm";

type Selection = { kind: "upload" } | { kind: "dataset"; id: string };

export function Workspace({ api }: { api: Api }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selection, setSelection] = useState<Selection>({ kind: "upload" });
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { datasets } = await api.listDatasets();
      setDatasets(datasets);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Keep the list's status badges in step with the open dataset.
  const onDatasetChange = useCallback((changed: Dataset) => {
    setDatasets((current) => current.map((d) => (d.id === changed.id ? changed : d)));
  }, []);

  return (
    <div className="workspace">
      <nav className="sidebar" aria-label="Datasets">
        <button
          type="button"
          className={selection.kind === "upload" ? "nav-item active" : "nav-item"}
          onClick={() => setSelection({ kind: "upload" })}
        >
          + New dataset
        </button>
        {datasets.map((dataset) => (
          <button
            type="button"
            key={dataset.id}
            className={
              selection.kind === "dataset" && selection.id === dataset.id
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() => setSelection({ kind: "dataset", id: dataset.id })}
          >
            <span className="nav-name">{dataset.name}</span>
            <span className={`badge ${dataset.status}`}>{dataset.status}</span>
          </button>
        ))}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </nav>
      <section className="main">
        {selection.kind === "upload" ? (
          <UploadForm
            api={api}
            onUploaded={async (id) => {
              await refresh();
              setSelection({ kind: "dataset", id });
            }}
          />
        ) : (
          <DatasetView
            key={selection.id}
            api={api}
            datasetId={selection.id}
            onChange={onDatasetChange}
          />
        )}
      </section>
    </div>
  );
}
