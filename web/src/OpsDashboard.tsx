import { AlertCircle, RefreshCw, TriangleAlert } from "lucide-react";
import { lazy, type ReactNode, Suspense, useCallback, useEffect, useState } from "react";
import { type Api, errorMessage, type VegaLiteSpec } from "./api";
import { Button } from "./components/ui/button";
import { Skeleton } from "./components/ui/skeleton";
import { useDark } from "./hooks/use-dark";
import { cn } from "./lib/utils";
import {
  alerts,
  type FailureRow,
  formatCount,
  formatDuration,
  formatPercent,
  formatUsd,
  GROUPS,
  groupOutcomes,
  groupSeries,
  type OpsSummary,
  OUTCOME_LABELS,
  STOP_LABELS,
  type Window,
} from "./ops";

const Chart = lazy(() => import("./Chart"));

const REFRESH_MS = 30_000;
const WINDOWS: { id: Window; label: string }[] = [
  { id: "24h", label: "24 hours" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
];

// Outcome colours, in GROUPS order (answered, abstained, stopped, error, in progress), validated
// adjacent-pair with the dataviz validator against the card surface of each mode (#ffffff,
// #111111): all gates pass. The light amber sits under 3:1, so the outcome chart has a legend and
// the outcome table beside it.
const OUTCOME_COLOURS = {
  light: ["#0ca30c", "#2a8fbf", "#d99a00", "#d03b3b", "#6250d6"],
  dark: ["#0ca30c", "#3aa0d0", "#c98500", "#d6363f", "#9085e9"],
};
const SURFACE = { light: "#ffffff", dark: "#111111" };

function timeAxis(summary: OpsSummary) {
  const hourly = summary.bucket === "hour";
  return {
    field: "bucket",
    type: "temporal",
    timeUnit: hourly ? "utcyearmonthdatehours" : "utcyearmonthdate",
    title: null,
    // A utc timeUnit makes Vega-Lite format these in UTC, matching the buckets.
    axis: { format: hourly ? "%H:%M" : "%b %d", labelOverlap: "greedy", labelSeparation: 8 },
  };
}

function outcomeChart(summary: OpsSummary, dark: boolean): VegaLiteSpec {
  const mode = dark ? "dark" : "light";
  return {
    height: 180,
    data: { values: groupSeries(summary.runs.series) },
    mark: { type: "bar", stroke: SURFACE[mode], strokeWidth: 2, cornerRadiusEnd: 0 },
    encoding: {
      x: timeAxis(summary),
      y: { field: "count", type: "quantitative", aggregate: "sum", title: "Runs" },
      color: {
        field: "group",
        type: "nominal",
        title: null,
        scale: { domain: [...GROUPS], range: OUTCOME_COLOURS[mode] },
        // Wraps on a phone, where five labels do not fit on one line.
        legend: { orient: "top", direction: "horizontal", columns: window.innerWidth < 600 ? 3 : 5 },
      },
      order: { field: "group", sort: "ascending" },
      tooltip: [
        { field: "bucket", type: "temporal", title: "From", timeUnit: "utcyearmonthdatehours", format: "%b %d %H:%M UTC" },
        { field: "group", title: "Outcome" },
        { field: "count", title: "Runs" },
      ],
    },
  };
}

function latencyChart(summary: OpsSummary): VegaLiteSpec {
  return {
    height: 160,
    data: { values: summary.latency.series },
    transform: [{ fold: ["run_p50", "run_p95"], as: ["measure", "seconds"] }],
    mark: { type: "line", point: true },
    encoding: {
      x: timeAxis(summary),
      y: { field: "seconds", type: "quantitative", title: "Run time (s)" },
      color: {
        field: "measure",
        type: "nominal",
        title: null,
        scale: { domain: ["run_p50", "run_p95"] },
        legend: { orient: "top", labelExpr: "datum.label == 'run_p50' ? 'Median' : '95th percentile'" },
      },
      tooltip: [
        { field: "bucket", type: "temporal", title: "From", timeUnit: "utcyearmonthdatehours", format: "%b %d %H:%M UTC" },
        { field: "measure", title: "Measure" },
        { field: "seconds", title: "Seconds", format: ".1f" },
      ],
    },
  };
}

function barChart(
  summary: OpsSummary,
  values: object[],
  field: string,
  title: string,
  format: string,
): VegaLiteSpec {
  return {
    height: 140,
    data: { values },
    mark: { type: "bar" },
    encoding: {
      x: timeAxis(summary),
      y: { field, type: "quantitative", aggregate: "sum", title },
      tooltip: [
        { field: "bucket", type: "temporal", title: "From", timeUnit: "utcyearmonthdatehours", format: "%b %d %H:%M UTC" },
        { field, title, format },
      ],
    },
  };
}

function errorChart(summary: OpsSummary): VegaLiteSpec {
  return {
    height: 140,
    data: { values: summary.model_errors.series },
    mark: { type: "bar" },
    encoding: {
      x: timeAxis(summary),
      y: { field: "count", type: "quantitative", aggregate: "sum", title: "Failed calls" },
      color: { field: "kind", type: "nominal", title: null, legend: { orient: "top" } },
      tooltip: [
        { field: "bucket", type: "temporal", title: "From", timeUnit: "utcyearmonthdatehours", format: "%b %d %H:%M UTC" },
        { field: "kind", title: "Kind" },
        { field: "count", title: "Calls" },
      ],
    },
  };
}

export function OpsDashboard({ api }: { api: Api }) {
  const [window_, setWindow] = useState<Window>("24h");
  const [summary, setSummary] = useState<OpsSummary | null>(null);
  const [failures, setFailures] = useState<FailureRow[]>([]);
  const [moreFailures, setMoreFailures] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const dark = useDark();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [next, recent] = await Promise.all([api.getOpsSummary(window_), api.getOpsFailures()]);
      setSummary(next);
      setFailures(recent.failures);
      setMoreFailures(recent.failures.length === 25);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [api, window_]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function loadOlder() {
    const last = failures.at(-1);
    if (!last) return;
    try {
      const older = await api.getOpsFailures(last.created_at);
      setFailures((current) => [...current, ...older.failures]);
      setMoreFailures(older.failures.length === 25);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div className="mr-auto">
          <h1 className="text-2xl font-semibold tracking-tight">Operations</h1>
          <p className="text-muted-foreground text-sm" aria-live="polite">
            {summary
              ? `Updated ${new Date(summary.generated_at).toLocaleTimeString()}`
              : "Loading…"}
            {" · refreshes every 30 s"}
          </p>
        </div>
        <div className="bg-muted inline-flex rounded-lg border p-0.5" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              aria-pressed={window_ === w.id}
              onClick={() => setWindow(w.id)}
              className={cn(
                "rounded-md px-3 py-1 text-sm transition-colors",
                window_ === w.id
                  ? "bg-background text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={cn(loading && "animate-spin")} />
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </header>
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
      {summary ? (
        <Panels
          summary={summary}
          dark={dark}
          failures={failures}
          moreFailures={moreFailures}
          onLoadOlder={loadOlder}
        />
      ) : (
        !error && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-hidden>
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
        )
      )}
    </div>
  );
}

function Panels({
  summary,
  dark,
  failures,
  moreFailures,
  onLoadOlder,
}: {
  summary: OpsSummary;
  dark: boolean;
  failures: FailureRow[];
  moreFailures: boolean;
  onLoadOlder: () => void;
}) {
  const { now, runs, latency, cost, quality, security, model_errors } = summary;
  const found = alerts(now, Date.now());
  const finished = runs.total - (runs.outcomes.in_progress ?? 0);
  const windowLabel = WINDOWS.find((w) => w.id === summary.window)?.label ?? summary.window;

  return (
    <>
      {found.length > 0 && (
        <ul className="flex flex-col gap-2">
          {found.map((alert) => (
            <li
              key={alert.text}
              className={cn(
                "flex items-center gap-2.5 rounded-lg border px-4 py-2.5 text-sm",
                alert.level === "critical"
                  ? "border-danger-line bg-danger-soft"
                  : "border-warning-line bg-warning-soft",
              )}
            >
              {alert.level === "critical" ? (
                <AlertCircle className="text-danger size-4 shrink-0" aria-hidden />
              ) : (
                <TriangleAlert className="text-warning size-4 shrink-0" aria-hidden />
              )}
              <span className="sr-only">{alert.level}: </span>
              {alert.text}
            </li>
          ))}
        </ul>
      )}

      <Section title="Right now">
        <div className={TILES}>
          <Tile label="Queued jobs" value={formatCount(now.queued)} />
          <Tile label="Running jobs" value={formatCount(now.running)} />
          <Tile label="Oldest wait" value={formatDuration(now.oldest_queued_seconds)} />
          <Tile
            label="Jobs that lost their worker"
            value={formatCount(now.expired_leases)}
            tone={now.expired_leases > 0 ? "critical" : undefined}
          />
          <Tile
            label="Worker last active"
            value={now.last_worker_activity ? timeAgo(now.last_worker_activity) : "never"}
          />
          <Tile label="Datasets to review" value={formatCount(now.datasets_awaiting_review)} />
        </div>
      </Section>

      <Section title={`Answers, last ${windowLabel}`}>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <div className="bg-card flex flex-col justify-center rounded-xl border p-6">
            <p className="text-5xl font-semibold tracking-tight tabular-nums">
              {formatPercent(runs.answer_rate)}
            </p>
            <p className="text-muted-foreground mt-2 text-sm">
              of {formatCount(finished)} finished runs were answered
            </p>
          </div>
          <div className={TILES}>
            <Tile label="Runs" value={formatCount(runs.total)} />
            <Tile label="Abstained" value={formatPercent(runs.abstain_rate)} />
            <Tile label="Retried" value={formatCount(runs.retried)} />
            <Tile label="Run time, 95th percentile" value={formatDuration(latency.run_p95)} />
            <Tile label="Spend" value={formatUsd(cost.total_usd)} />
            <Tile
              label="Failed model calls"
              value={formatCount(model_errors.total)}
              tone={model_errors.total > 0 ? "warning" : undefined}
            />
          </div>
        </div>
      </Section>

      <Section title="Run outcomes">
        {runs.total === 0 ? (
          <Empty>No runs in this window.</Empty>
        ) : (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <ChartBox spec={outcomeChart(summary, dark)} />
            <div className="self-start overflow-x-auto rounded-xl border">
            <table className={TABLE}>
              <caption className="sr-only">Runs by outcome</caption>
              <thead>
                <tr>
                  <th scope="col">Outcome</th>
                  <th scope="col" className={NUM}>
                    Runs
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(runs.outcomes)
                  .sort((a, b) => b[1] - a[1])
                  .map(([outcome, count]) => (
                    <tr key={outcome}>
                      <td>{OUTCOME_LABELS[outcome] ?? outcome}</td>
                      <td className={NUM}>{formatCount(count)}</td>
                    </tr>
                  ))}
              </tbody>
              <tfoot>
                <tr>
                  <td>
                    {groupOutcomes(runs.outcomes)
                      .map((g) => `${g.group} ${g.count}`)
                      .join(", ")}
                  </td>
                  <td className={NUM}>{formatCount(runs.total)}</td>
                </tr>
              </tfoot>
            </table>
            </div>
          </div>
        )}
      </Section>

      <Section title="Speed">
        <div className={TILES}>
          <Tile label="Queue wait, median" value={formatDuration(latency.queue_wait_p50)} />
          <Tile label="Queue wait, 95th percentile" value={formatDuration(latency.queue_wait_p95)} />
          <Tile label="Run time, median" value={formatDuration(latency.run_p50)} />
          <Tile label="Run time, 95th percentile" value={formatDuration(latency.run_p95)} />
        </div>
        {latency.series.length > 0 && <ChartBox spec={latencyChart(summary)} />}
      </Section>

      <Section title="Model calls">
        {summary.models.length === 0 ? (
          <Empty>No model calls in this window.</Empty>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <table className={TABLE}>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col" className={NUM}>Calls</th>
                  <th scope="col" className={NUM}>Failed</th>
                  <th scope="col" className={NUM}>Median</th>
                  <th scope="col" className={NUM}>95th pct.</th>
                  <th scope="col" className={NUM}>Tokens in / out</th>
                  <th scope="col" className={NUM}>Cached</th>
                  <th scope="col" className={NUM}>Cost</th>
                </tr>
              </thead>
              <tbody>
                {summary.models.map((m) => (
                  <tr key={`${m.provider}/${m.model}`}>
                    <td>
                      <code>{m.model}</code> <span className="text-muted-foreground text-xs">{m.provider}</span>
                    </td>
                    <td className={NUM}>{formatCount(m.calls)}</td>
                    <td className={NUM}>{formatCount(m.errors)}</td>
                    <td className={NUM}>{formatDuration(m.latency_p50)}</td>
                    <td className={NUM}>{formatDuration(m.latency_p95)}</td>
                    <td className={NUM}>
                      {formatCount(m.input_tokens)} / {formatCount(m.output_tokens)}
                    </td>
                    <td className={NUM}>{formatPercent(m.cached_share)}</td>
                    <td className={NUM}>{formatUsd(m.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {model_errors.total > 0 && (
          <>
            <h4 className="mb-2 text-sm font-medium">Failed calls by kind</h4>
            <p className="text-muted-foreground mb-2 text-sm">
              {Object.entries(model_errors.by_kind)
                .map(([kind, n]) => `${kind.replaceAll("_", " ")}: ${n}`)
                .join(" · ")}
            </p>
            <ChartBox spec={errorChart(summary)} />
          </>
        )}
      </Section>

      <Section title="Cost">
        <div className={TILES}>
          <Tile label="Spend" value={formatUsd(cost.total_usd)} />
          <Tile label="Average answered run" value={formatUsd(cost.per_answered_run_usd)} />
          <Tile label="Default daily budget" value={formatUsd(summary.daily_budget_default_usd)} />
        </div>
        {cost.series.length > 0 && (
          <ChartBox spec={barChart(summary, cost.series, "cost_usd", "Spend (USD)", "$.4f")} />
        )}
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <h4 className="mb-2 text-sm font-medium">Top workspaces</h4>
            {cost.by_workspace.length === 0 ? (
              <Empty>No spending in this window.</Empty>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
              <table className={TABLE}>
                <thead>
                  <tr>
                    <th scope="col">Workspace</th>
                    <th scope="col" className={NUM}>Runs</th>
                    <th scope="col" className={NUM}>Spend</th>
                  </tr>
                </thead>
                <tbody>
                  {cost.by_workspace.map((w) => (
                    <tr key={w.workspace_id}>
                      <td>{w.name}</td>
                      <td className={NUM}>{formatCount(w.runs)}</td>
                      <td className={NUM}>{formatUsd(w.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
          <div>
            <h4 className="mb-2 text-sm font-medium">Today against the daily budget (UTC)</h4>
            {cost.today.length === 0 ? (
              <Empty>No spending today.</Empty>
            ) : (
              <ul className="flex flex-col gap-4 rounded-xl border p-4">
                {cost.today.map((w) => (
                  <Meter key={w.workspace_id} name={w.name} spent={w.spent_usd} budget={w.budget_usd} />
                ))}
              </ul>
            )}
          </div>
        </div>
      </Section>

      <Section title="Quality signals">
        <div className={TILES}>
          <Tile
            label="Answers that failed a check"
            value={`${formatCount(quality.verify_failed)} of ${formatCount(quality.verify_checks)}`}
          />
          <Tile label="Runs that needed a repair" value={formatCount(quality.runs_repaired)} />
          <Tile
            label="Tool calls that failed"
            value={`${formatCount(quality.tool_errors)} of ${formatCount(quality.tool_calls)}`}
          />
          <Tile
            label="Dataset builds that failed"
            value={`${formatCount(quality.ingest_failed)} of ${formatCount(quality.ingest_jobs)}`}
            tone={quality.ingest_failed > 0 ? "warning" : undefined}
          />
        </div>
        <p className="text-muted-foreground text-sm">
          Eval scores are measured offline; see the evaluation report in the repository.
        </p>
      </Section>

      <Section title="Sign-ins">
        <div className={TILES}>
          <Tile
            label="Refused keys, links, and sessions"
            value={formatCount(security.auth_failed)}
            tone={security.auth_failed > 20 ? "warning" : undefined}
          />
          <Tile label="Sign-ins" value={formatCount(security.sign_ins)} />
        </div>
        {security.series.length > 0 && (
          <ChartBox spec={barChart(summary, security.series, "count", "Refused", "d")} />
        )}
      </Section>

      <Section title="Recent failures">
        {failures.length === 0 ? (
          <Empty>Nothing has failed. Failed jobs, retries, and runs that stop without an answer appear here.</Empty>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <table className={TABLE}>
              <thead>
                <tr>
                  <th scope="col">When (UTC)</th>
                  <th scope="col">Workspace</th>
                  <th scope="col">Job</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Trace</th>
                </tr>
              </thead>
              <tbody>
                {failures.map((f) => (
                  <tr key={f.id}>
                    <td className="whitespace-nowrap">{f.created_at.slice(0, 16).replace("T", " ")}</td>
                    <td>{f.workspace_name}</td>
                    <td>
                      {f.kind} <code className="text-xs">{f.id.slice(0, 8)}</code>
                      <div className="text-muted-foreground text-xs">
                        {f.status === "queued" ? "retrying" : f.status}, attempt {f.attempts}
                      </div>
                    </td>
                    <td>
                      {f.stop_reason && <div>{STOP_LABELS[f.stop_reason] ?? f.stop_reason}</div>}
                      {f.error && <code className="text-danger text-xs break-words">{f.error}</code>}
                    </td>
                    <td>
                      {f.traces.length === 0 ? (
                        <span className="text-muted-foreground">–</span>
                      ) : (
                        f.traces.map((t) => (
                          <a
                            key={t.attempt}
                            href={t.url}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="mr-2 underline-offset-4 hover:underline"
                          >
                            Attempt {t.attempt}
                          </a>
                        ))
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {moreFailures && (
          <Button variant="outline" size="sm" className="self-start" onClick={onLoadOlder}>
            Show older failures
          </Button>
        )}
      </Section>
    </>
  );
}

const TILES = "grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4";
const TABLE =
  "w-full border-collapse text-sm [&_td]:border-t [&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_th]:bg-muted [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted-foreground [&_tfoot_td]:text-muted-foreground";
const NUM = "text-right font-mono tabular-nums whitespace-nowrap";

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `ops-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section className="flex flex-col gap-3" aria-labelledby={id}>
      <h2 id={id} className="text-base font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "critical" | "warning";
}) {
  return (
    <div
      className={cn(
        "bg-card flex flex-col gap-1 rounded-xl border p-4",
        tone === "critical" && "border-danger-line",
        tone === "warning" && "border-warning-line",
      )}
    >
      <p className="text-muted-foreground text-xs">
        {label}
        {tone && <span className="sr-only"> ({tone})</span>}
      </p>
      <p className="flex items-center gap-1.5 text-2xl font-semibold tracking-tight tabular-nums">
        {tone === "critical" && <AlertCircle className="text-danger size-4" aria-hidden />}
        {tone === "warning" && <TriangleAlert className="text-warning size-4" aria-hidden />}
        {value}
      </p>
    </div>
  );
}

function Meter({ name, spent, budget }: { name: string; spent: number; budget: number }) {
  const share = budget > 0 ? spent / budget : 1;
  const tone = share >= 1 ? "critical" : share >= 0.8 ? "warning" : null;
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="truncate">{name}</span>
        <span className="text-muted-foreground flex items-center gap-1 font-mono text-xs tabular-nums">
          {tone === "critical" && <AlertCircle className="text-danger size-3.5" aria-hidden />}
          {tone === "warning" && <TriangleAlert className="text-warning size-3.5" aria-hidden />}
          {formatUsd(spent)} of {formatUsd(budget)}
          {tone === "critical" && " · limit reached"}
        </span>
      </div>
      <div
        className="bg-secondary h-1.5 overflow-hidden rounded-full"
        role="meter"
        aria-label={`${name}: share of today's budget`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.round(share * 100))}
      >
        <div
          className={cn(
            "h-full rounded-full",
            tone === "critical" ? "bg-danger" : tone === "warning" ? "bg-warning" : "bg-foreground",
          )}
          style={{ width: `${Math.min(100, share * 100)}%` }}
        />
      </div>
    </li>
  );
}

function ChartBox({ spec }: { spec: VegaLiteSpec }) {
  return (
    <div className="bg-card rounded-xl border p-4">
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <Chart spec={spec} />
      </Suspense>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="text-muted-foreground rounded-xl border border-dashed px-4 py-6 text-center text-sm">
      {children}
    </p>
  );
}

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return seconds < 5 ? "just now" : `${formatDuration(seconds)} ago`;
}
