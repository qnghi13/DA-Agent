import { Check, Copy, Download, ExternalLink, Pencil, Play, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { type Api, type ExportJob, type QueryJob, type QueryRecord, exportFileUrl } from "@/api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { cell, DataTable } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { waitForJob } from "@/export";
import { canActOnQueries } from "@/run";
import { StepLine, type Evidence } from "./run-message";

const PREVIEW_ROWS = 20;

/** Everything behind one answer: steps, SQL, the rows the model saw, and the report. */
export function EvidenceContent({ api, evidence }: { api: Api; evidence: Evidence }) {
  const { run, trail } = evidence;
  const queries = run.result?.queries ?? [];
  const canReport = run.status === "succeeded" && Boolean(run.result);
  // A stopped run keeps its queries to read, but the API exports and reruns only finished runs.
  const actions = canActOnQueries(run.status);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Tabs defaultValue="steps" className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="border-b px-4">
          <TabsList className="h-10 gap-4 rounded-none bg-transparent p-0">
            {["steps", "sql", "data", ...(canReport ? ["report"] : [])].map((tab) => (
              <TabsTrigger
                key={tab}
                value={tab}
                className="data-[state=active]:border-foreground text-muted-foreground data-[state=active]:text-foreground h-10 flex-none rounded-none border-0 border-b-2 border-transparent px-0 capitalize shadow-none data-[state=active]:bg-transparent data-[state=active]:shadow-none dark:data-[state=active]:bg-transparent"
              >
                {tab === "sql" ? "SQL" : tab}
                {tab === "sql" && queries.length > 0 && (
                  <span className="text-muted-foreground ml-1 text-xs">{queries.length}</span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <TabsContent value="steps" className="p-4">
            {trail.steps.length ? (
              <ol className="flex flex-col gap-2">
                {trail.steps.map((step) => (
                  <StepLine key={step.id} step={step} />
                ))}
              </ol>
            ) : (
              <Empty>No steps were recorded for this run.</Empty>
            )}
            {trail.steps.some((s) => s.kind === "verify" && s.problems.length) && (
              <div className="mt-4">
                <h3 className="mb-2 text-sm font-medium">What the checks found</h3>
                <ul className="text-muted-foreground list-disc pl-5 text-sm">
                  {trail.steps.flatMap((s) =>
                    s.kind === "verify" ? s.problems.map((p, i) => <li key={`${s.id}-${i}`}>{p}</li>) : [],
                  )}
                </ul>
              </div>
            )}
          </TabsContent>
          <TabsContent value="sql" className="flex flex-col gap-3 p-4">
            {queries.length ? (
              queries.map((query, index) => (
                <SqlBlock
                  key={index}
                  api={api}
                  runId={run.id}
                  index={index}
                  query={query}
                  actions={actions}
                />
              ))
            ) : (
              <Empty>The agent ran no queries.</Empty>
            )}
          </TabsContent>
          <TabsContent value="data" className="flex flex-col gap-5 p-4">
            {queries.length ? (
              queries.map((query, index) => (
                <QueryRows
                  key={index}
                  api={api}
                  runId={run.id}
                  index={index}
                  query={query}
                  actions={actions}
                />
              ))
            ) : (
              <Empty>No query results.</Empty>
            )}
          </TabsContent>
          {canReport && (
            <TabsContent value="report" className="p-4">
              <Report api={api} runId={run.id} />
            </TabsContent>
          )}
        </div>
      </Tabs>
      <footer className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-2 border-t px-4 py-3 text-xs">
        <RunId id={run.id} />
        {(run.traces ?? []).map((link) => (
          <a
            key={link.attempt}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground inline-flex items-center gap-1 underline-offset-4 hover:underline"
          >
            {(run.traces ?? []).length > 1 ? `Trace, attempt ${link.attempt}` : "Open trace"}
            <ExternalLink className="size-3" />
          </a>
        ))}
      </footer>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground text-sm">{children}</p>;
}

function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  return [
    copied,
    (text: string) => {
      navigator.clipboard
        .writeText(text)
        .then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })
        .catch(() => undefined);
    },
  ];
}

/**
 * One of the agent's queries. Edit runs a changed copy on the same dataset as a query job; the
 * agent's SQL and answer stay as they were (docs/design/2026-11-14-edit-sql.md).
 */
function SqlBlock({
  api,
  runId,
  index,
  query,
  actions,
}: {
  api: Api;
  runId: string;
  index: number;
  query: QueryRecord;
  actions: boolean;
}) {
  const m = useT();
  const [copied, copy] = useCopy();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(query.sql);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryJob | null>(null);
  const polling = useRef<AbortController | null>(null);
  useEffect(() => () => polling.current?.abort(), []);

  async function run() {
    polling.current?.abort();
    const controller = new AbortController();
    polling.current = controller;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const { id } = await api.runQuery(runId, draft);
      const job = await waitForJob((queryId) => api.getQuery(queryId), id, {
        signal: controller.signal,
      });
      if (job.status === "failed") setError(job.error ?? "The query failed.");
      else setResult(job);
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorMessage(caught, m));
    } finally {
      if (polling.current === controller) setBusy(false);
    }
  }

  function reset() {
    polling.current?.abort();
    setBusy(false);
    setDraft(query.sql);
    setError(null);
    setResult(null);
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="bg-muted flex items-center justify-between gap-1 border-b px-3 py-1.5">
        <span className="text-muted-foreground flex-1 text-xs">
          Query {index + 1}
          {editing && draft !== query.sql ? " · edited" : ""}
        </span>
        {actions && !editing && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7"
            aria-label={`Edit query ${index + 1}`}
            onClick={() => setEditing(true)}
          >
            <Pencil />
            Edit
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Copy query ${index + 1}`}
          onClick={() => copy(editing ? draft : query.sql)}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
      {editing ? (
        <div className="flex flex-col gap-2 p-3">
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !busy) run();
            }}
            spellCheck={false}
            aria-label={`SQL for query ${index + 1}`}
            aria-invalid={error ? true : undefined}
            className="font-mono text-[12.5px] leading-relaxed md:text-[12.5px]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" className="h-7" disabled={busy || !draft.trim()} onClick={run}>
              <Play />
              {busy ? "Running…" : "Run"}
            </Button>
            <Button variant="ghost" size="sm" className="h-7" onClick={reset}>
              <RotateCcw />
              Reset
            </Button>
            <span className="text-muted-foreground text-xs">
              One SELECT on this dataset. Ctrl+Enter runs it.
            </span>
          </div>
          {error && (
            <p role="alert" className="text-danger font-mono text-xs break-words whitespace-pre-wrap">
              {error}
            </p>
          )}
          {result && <EditedRows api={api} job={result} />}
        </div>
      ) : (
        <pre className="p-3 font-mono text-[12.5px] leading-relaxed break-words whitespace-pre-wrap">
          {query.sql}
        </pre>
      )}
    </div>
  );
}

function EditedRows({ api, job }: { api: Api; job: QueryJob }) {
  const shown = job.rows.slice(0, PREVIEW_ROWS);
  return (
    <section className="flex flex-col gap-2 pt-1">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-muted-foreground text-xs font-medium">
          Edited query · {job.truncated ? "first " : ""}
          {job.rows.length.toLocaleString()} row{job.rows.length === 1 ? "" : "s"}
        </h3>
        <ExportButton
          start={() => api.startQueryExport(job.id)}
          getExport={(id) => api.getExport(id)}
          label="the edited query"
        />
      </div>
      {shown.length ? (
        <DataTable columns={job.columns} rows={shown.map((row) => row.map(cell))} />
      ) : (
        <Empty>No rows.</Empty>
      )}
      {(job.truncated || job.rows.length > PREVIEW_ROWS) && (
        <p className="text-muted-foreground text-xs">
          Showing the first {shown.length} rows{job.truncated ? " of a longer result" : ""}.
        </p>
      )}
    </section>
  );
}

function QueryRows({
  api,
  runId,
  index,
  query,
  actions,
}: {
  api: Api;
  runId: string;
  index: number;
  query: QueryRecord;
  actions: boolean;
}) {
  const shown = query.rows.slice(0, PREVIEW_ROWS);
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-muted-foreground text-xs font-medium">
          Query {index + 1} · {query.truncated ? "first " : ""}
          {query.rows.length.toLocaleString()} row
          {query.rows.length === 1 ? "" : "s"}
        </h3>
        {actions && (
          <ExportButton
            start={() => api.startExport(runId, index)}
            getExport={(id) => api.getExport(id)}
            label={`query ${index + 1}`}
          />
        )}
      </div>
      {shown.length ? (
        <DataTable columns={query.columns} rows={shown.map((row) => row.map(cell))} />
      ) : (
        <Empty>No rows.</Empty>
      )}
      {(query.truncated || query.rows.length > PREVIEW_ROWS) && (
        <p className="text-muted-foreground text-xs">
          Showing the first {shown.length} rows{query.truncated ? " of a longer result" : ""}.
        </p>
      )}
    </section>
  );
}

/**
 * Downloads the whole result of one query as CSV. The server runs the stored query again in a
 * sandbox, so the file holds every row, not the preview (docs/design/2026-11-14-result-export.md).
 */
function ExportButton({
  start,
  getExport,
  label,
}: {
  start: () => Promise<{ id: string }>;
  getExport: (id: string) => Promise<ExportJob>;
  label: string;
}) {
  const m = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Polling has no deadline, so it stops when this view goes away.
  const polling = useRef<AbortController | null>(null);
  useEffect(() => () => polling.current?.abort(), []);

  async function download() {
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    polling.current = controller;
    try {
      const { id } = await start();
      const job = await waitForJob(getExport, id, {
        signal: controller.signal,
      });
      if (job.status === "failed") {
        setError(job.error ?? "The export failed.");
        return;
      }
      const link = document.createElement("a");
      link.href = exportFileUrl(id);
      link.download = job.filename ?? "";
      link.click();
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorMessage(caught, m));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7"
        disabled={busy}
        onClick={download}
        aria-label={`Download ${label} as CSV`}
      >
        <Download />
        {busy ? "Preparing file…" : "Download CSV"}
      </Button>
      {error && <p className="text-danger max-w-72 text-right text-xs">{error}</p>}
    </div>
  );
}

/** The run id, for finding the run in the trace viewer by da_agent.job_id. */
function RunId({ id }: { id: string }) {
  const [copied, copy] = useCopy();
  return (
    <span className="inline-flex items-center gap-1">
      Run <code className="text-foreground">{id.slice(0, 8)}</code>
      <button
        type="button"
        onClick={() => copy(id)}
        aria-label={`Copy run id ${id}`}
        className="hover:text-foreground underline-offset-4 hover:underline"
      >
        {copied ? "Copied" : "Copy ID"}
      </button>
    </span>
  );
}

/**
 * The run's HTML report, fetched with the session cookie and shown in a sandboxed frame. Without
 * allow-same-origin the frame cannot read this page or call the API; the report's own CSP meta tag
 * still limits its scripts to the chart libraries.
 */
function Report({ api, runId }: { api: Api; runId: string }) {
  const m = useT();
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
      .catch((caught) => !cancelled && setError(errorMessage(caught, m)));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, runId]);

  if (error) return <p className="text-danger text-sm">{error}</p>;
  if (!html) return <div className="bg-muted h-96 animate-pulse rounded-lg" />;
  return (
    <div className="flex flex-col gap-3">
      {download && (
        <Button asChild variant="outline" size="sm" className="self-start">
          <a href={download} download={`da-agent-report-${runId}.html`}>
            <Download />
            Download report
          </a>
        </Button>
      )}
      <iframe
        title="Run report"
        sandbox="allow-scripts"
        srcDoc={html}
        className="h-[70vh] w-full rounded-lg border bg-white"
      />
    </div>
  );
}
