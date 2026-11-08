import { lazy, Suspense, useEffect, useReducer, useState } from "react";
import { type Api, type QueryRecord, type Run, errorMessage } from "./api";
import { ReportViewer } from "./ReportViewer";
import {
  type Trail,
  type TrailStep,
  formatAnswer,
  initialTrail,
  reduceTrail,
  stopReasonText,
  toolLabel,
} from "./run";
import type { SseEvent } from "./sse";

// Vega is large; load it only when a chart is on screen.
const Chart = lazy(() => import("./Chart"));

const PREVIEW_ROWS = 20;

export function RunCard({ api, runId }: { api: Api; runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [trail, dispatch] = useReducer(
    (state: Trail, event: SseEvent | "reset") =>
      event === "reset" ? initialTrail() : reduceTrail(state, event),
    undefined,
    initialTrail,
  );
  const [error, setError] = useState<string | null>(null);
  const [showReport, setShowReport] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    dispatch("reset");
    (async () => {
      try {
        setRun(await api.getRun(runId));
        await api.streamRunEvents(runId, dispatch, controller.signal);
        if (!controller.signal.aborted) setRun(await api.getRun(runId));
      } catch (caught) {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      }
    })();
    return () => controller.abort();
  }, [api, runId]);

  if (!run) {
    return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  }

  const finished = run.status === "succeeded" || run.status === "failed";
  const running = !finished && !error;
  const result = run.result;
  const charts = (result?.steps ?? []).flatMap((step) => (step.chart ? [step.chart] : []));
  const rowsBySql = new Map((result?.queries ?? []).map((q) => [q.sql, q]));

  return (
    <article className="run" aria-busy={running}>
      <header className="run-head">
        <h3 className="run-question">{run.question}</h3>
        <p className="muted small">As of {run.as_of}</p>
      </header>

      <ol className="trail">
        {trail.steps.map((step) => (
          <TrailItem key={step.id} step={step} rows={step.kind === "tool" && step.sql ? rowsBySql.get(step.sql) : undefined} />
        ))}
        {running && (
          <li className="trail-item live">
            <span className="trail-mark" aria-hidden />
            <p>{run.status === "queued" ? "Waiting for a worker…" : "Working…"}</p>
          </li>
        )}
      </ol>

      {error && <p className="error">{error}</p>}
      {run.status === "failed" && (
        <p className="error">The run failed{run.error ? `: ${run.error}` : "."}</p>
      )}

      {result && <AnswerBlock run={run} />}
      {trail.question && !result?.question_for_user && (
        <p className="clarify">The agent asks: {trail.question}</p>
      )}

      {charts.length > 0 && (
        <Suspense fallback={<p className="muted">Drawing the chart…</p>}>
          {charts.map((spec, index) => (
            <Chart key={index} spec={spec} />
          ))}
        </Suspense>
      )}

      <div className="run-actions">
        {run.status === "succeeded" && result && (
          <button type="button" className="button-quiet" onClick={() => setShowReport((v) => !v)}>
            {showReport ? "Hide report" : "View report"}
          </button>
        )}
        {(run.traces ?? []).map((link) => (
          <a
            key={link.attempt}
            className="button-quiet"
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {(run.traces ?? []).length > 1 ? `Open trace, attempt ${link.attempt}` : "Open trace"}
          </a>
        ))}
        <RunId id={run.id} />
      </div>
      {showReport && <ReportViewer api={api} runId={run.id} />}
    </article>
  );
}

/** The run id, for finding the run in the trace viewer by da_agent.job_id. */
function RunId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked: the id is still visible to select by hand.
    }
  }
  return (
    <span className="run-id muted small">
      Run <code>{id.slice(0, 8)}</code>
      <button type="button" className="link" onClick={copy} aria-label={`Copy run id ${id}`}>
        {copied ? "Copied" : "Copy ID"}
      </button>
    </span>
  );
}

function AnswerBlock({ run }: { run: Run }) {
  const result = run.result!;
  if (result.question_for_user) {
    return (
      <div className="answer">
        <p className="clarify">The agent needs more detail: {result.question_for_user}</p>
        <p className="muted small">Ask again with that detail in the question.</p>
      </div>
    );
  }
  if (!result.answer) {
    return (
      <div className="answer">
        <p className="answer-none">No answer</p>
        <p className="muted">{stopReasonText(result.stop_reason) ?? result.error}</p>
      </div>
    );
  }
  const formatted = formatAnswer(result.answer);
  return (
    <div className="answer">
      {"text" in formatted && (
        <p className={result.answer.kind === "unanswerable" ? "answer-none" : "answer-value"}>
          {formatted.text}
        </p>
      )}
      {"list" in formatted && (
        <ul className="answer-list">
          {formatted.list.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
      {"table" in formatted && <DataTable columns={formatted.table.columns} rows={formatted.table.rows} />}
      {result.answer.reason && <p className="answer-reason">{result.answer.reason}</p>}
    </div>
  );
}

function TrailItem({ step, rows }: { step: TrailStep; rows?: QueryRecord }) {
  if (step.kind === "plan") {
    return (
      <li className="trail-item">
        <span className="trail-mark" aria-hidden />
        <p>Planned the steps</p>
        {step.steps.length > 0 && (
          <ol className="plan-steps">
            {step.steps.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ol>
        )}
      </li>
    );
  }
  if (step.kind === "verify") {
    return (
      <li className={step.passed ? "trail-item" : "trail-item problem"}>
        <span className="trail-mark" aria-hidden />
        <p>{step.passed ? "Checked the answer against the data" : "The checks found problems, so the agent kept working"}</p>
        {step.problems.length > 0 && (
          <ul className="small">
            {step.problems.map((problem, i) => (
              <li key={i}>{problem}</li>
            ))}
          </ul>
        )}
      </li>
    );
  }
  return (
    <li className={step.ok ? "trail-item" : "trail-item problem"}>
      <span className="trail-mark" aria-hidden />
      <p>
        {toolLabel(step.tool)}
        {step.rowCount !== null && (
          <span className="muted">
            {" "}
            and got {step.rowCount.toLocaleString()} row{step.rowCount === 1 ? "" : "s"}
          </span>
        )}
      </p>
      {step.error && <p className="error small">{step.error}</p>}
      {step.sql && (
        <details className="sql">
          <summary>{rows ? "SQL and result" : "SQL"}</summary>
          <pre className="code">{step.sql}</pre>
          {rows && rows.rows.length > 0 && (
            <>
              <DataTable
                columns={rows.columns}
                rows={rows.rows.slice(0, PREVIEW_ROWS).map((row) => row.map(cell))}
              />
              {(rows.truncated || rows.rows.length > PREVIEW_ROWS) && (
                <p className="muted small">Showing the first {Math.min(PREVIEW_ROWS, rows.rows.length)} rows.</p>
              )}
            </>
          )}
        </details>
      )}
    </li>
  );
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

function DataTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((value, c) => (
                <td key={c} className={/^-?[\d.,]+%?$/.test(value) ? "num" : undefined}>
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
