import { useEffect, useState } from "react";
import { type Api, errorMessage } from "./api";

/**
 * The run's HTML report, fetched with the key and shown in a sandboxed frame. Without
 * allow-same-origin the frame cannot read this page or its key; the report's own CSP meta tag
 * still limits its scripts to the chart libraries.
 */
export function ReportViewer({ api, runId }: { api: Api; runId: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [download, setDownload] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    api
      .getReport(runId)
      .then((page) => {
        if (cancelled) return;
        setHtml(page);
        url = URL.createObjectURL(new Blob([page], { type: "text/html" }));
        setDownload(url);
      })
      .catch((caught) => !cancelled && setError(errorMessage(caught)));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, runId]);

  if (error) return <p className="error">{error}</p>;
  if (!html) return <p className="muted">Loading the report…</p>;
  return (
    <div className="report">
      <iframe title="Run report" sandbox="allow-scripts" srcDoc={html} />
      {download && (
        <a className="button-quiet" href={download} download={`da-agent-report-${runId}.html`}>
          Download report
        </a>
      )}
    </div>
  );
}
