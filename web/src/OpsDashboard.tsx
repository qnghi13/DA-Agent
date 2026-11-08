import { lazy, type ReactNode, Suspense, useCallback, useEffect, useState } from "react";
import { type Api, errorMessage, type VegaLiteSpec } from "./api";
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

// Outcome colours, in GROUPS order, validated adjacent-pair with the dataviz validator against
// the card surface: answered, abstained, stopped, error, in progress. The light amber sits under
// 3:1, so every outcome chart has a legend and the outcome table beside it.
const OUTCOME_COLOURS = {
  light: ["#0ca30c", "#2a8fbf", "#d99a00", "#d03b3b", "#4744c9"],
  dark: ["#0ca30c", "#3aa0d0", "#c98500", "#d6363f", "#7270f0"],
};
const SURFACE = { light: "#ffffff", dark: "#1e2230" };

function usePrefersDark(): boolean {
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(query?.matches ?? false);
  useEffect(() => {
    const update = () => setDark(query.matches);
    query?.addEventListener("change", update);
    return () => query?.removeEventListener("change", update);
  }, [query]);
  return dark;
}

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
  const dark = usePrefersDark();

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
    <div className="ops">
      <div className="ops-head">
        <h2>Operations</h2>
        <div className="segmented" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              aria-pressed={window_ === w.id}
              onClick={() => setWindow(w.id)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <p className="muted small ops-updated" aria-live="polite">
          {summary ? `Updated ${new Date(summary.generated_at).toLocaleTimeString()}` : "Loading…"}
          {" · refreshes every 30 s"}
        </p>
        <button type="button" className="button-quiet" onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh now"}
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {summary && (
        <Panels
          summary={summary}
          dark={dark}
          failures={failures}
          moreFailures={moreFailures}
          onLoadOlder={loadOlder}
        />
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
        <ul className="ops-alerts">
          {found.map((alert) => (
            <li key={alert.text} className={`ops-alert ${alert.level}`}>
              <span className="ops-alert-icon" aria-hidden="true">
                {alert.level === "critical" ? "!" : "▲"}
              </span>
              <span className="visually-hidden">{alert.level}: </span>
              {alert.text}
            </li>
          ))}
        </ul>
      )}

      <Section title="Right now">
        <div className="tiles">
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
        <div className="ops-hero">
          <div>
            <p className="hero-figure">{formatPercent(runs.answer_rate)}</p>
            <p className="muted">
              of {formatCount(finished)} finished runs were answered
            </p>
          </div>
          <div className="tiles">
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
          <div className="ops-split">
            <ChartBox spec={outcomeChart(summary, dark)} />
            <table className="ops-table">
              <caption className="visually-hidden">Runs by outcome</caption>
              <thead>
                <tr>
                  <th scope="col">Outcome</th>
                  <th scope="col" className="num">
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
                      <td className="num">{formatCount(count)}</td>
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
                  <td className="num">{formatCount(runs.total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Section>

      <Section title="Speed">
        <div className="tiles">
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
          <div className="table-scroll">
            <table className="ops-table">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col" className="num">Calls</th>
                  <th scope="col" className="num">Failed</th>
                  <th scope="col" className="num">Median</th>
                  <th scope="col" className="num">95th pct.</th>
                  <th scope="col" className="num">Tokens in / out</th>
                  <th scope="col" className="num">Cached</th>
                  <th scope="col" className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {summary.models.map((m) => (
                  <tr key={`${m.provider}/${m.model}`}>
                    <td>
                      <code>{m.model}</code> <span className="muted small">{m.provider}</span>
                    </td>
                    <td className="num">{formatCount(m.calls)}</td>
                    <td className="num">{formatCount(m.errors)}</td>
                    <td className="num">{formatDuration(m.latency_p50)}</td>
                    <td className="num">{formatDuration(m.latency_p95)}</td>
                    <td className="num">
                      {formatCount(m.input_tokens)} / {formatCount(m.output_tokens)}
                    </td>
                    <td className="num">{formatPercent(m.cached_share)}</td>
                    <td className="num">{formatUsd(m.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {model_errors.total > 0 && (
          <>
            <h4 className="ops-subhead">Failed calls by kind</h4>
            <p className="muted small">
              {Object.entries(model_errors.by_kind)
                .map(([kind, n]) => `${kind.replaceAll("_", " ")}: ${n}`)
                .join(" · ")}
            </p>
            <ChartBox spec={errorChart(summary)} />
          </>
        )}
      </Section>

      <Section title="Cost">
        <div className="tiles">
          <Tile label="Spend" value={formatUsd(cost.total_usd)} />
          <Tile label="Average answered run" value={formatUsd(cost.per_answered_run_usd)} />
          <Tile label="Default daily budget" value={formatUsd(summary.daily_budget_default_usd)} />
        </div>
        {cost.series.length > 0 && (
          <ChartBox spec={barChart(summary, cost.series, "cost_usd", "Spend (USD)", "$.4f")} />
        )}
        <div className="ops-split even">
          <div>
            <h4 className="ops-subhead">Top workspaces</h4>
            {cost.by_workspace.length === 0 ? (
              <Empty>No spending in this window.</Empty>
            ) : (
              <table className="ops-table">
                <thead>
                  <tr>
                    <th scope="col">Workspace</th>
                    <th scope="col" className="num">Runs</th>
                    <th scope="col" className="num">Spend</th>
                  </tr>
                </thead>
                <tbody>
                  {cost.by_workspace.map((w) => (
                    <tr key={w.workspace_id}>
                      <td>{w.name}</td>
                      <td className="num">{formatCount(w.runs)}</td>
                      <td className="num">{formatUsd(w.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div>
            <h4 className="ops-subhead">Today against the daily budget (UTC)</h4>
            {cost.today.length === 0 ? (
              <Empty>No spending today.</Empty>
            ) : (
              <ul className="meters">
                {cost.today.map((w) => (
                  <Meter key={w.workspace_id} name={w.name} spent={w.spent_usd} budget={w.budget_usd} />
                ))}
              </ul>
            )}
          </div>
        </div>
      </Section>

      <Section title="Quality signals">
        <div className="tiles">
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
        <p className="muted small">
          Eval scores are measured offline; see the evaluation report in the repository.
        </p>
      </Section>

      <Section title="Sign-ins">
        <div className="tiles">
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
          <div className="table-scroll">
            <table className="ops-table failures">
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
                    <td className="nowrap">{f.created_at.slice(0, 16).replace("T", " ")}</td>
                    <td>{f.workspace_name}</td>
                    <td>
                      {f.kind} <code className="small">{f.id.slice(0, 8)}</code>
                      <div className="muted small">
                        {f.status === "queued" ? "retrying" : f.status}, attempt {f.attempts}
                      </div>
                    </td>
                    <td>
                      {f.stop_reason && <div>{STOP_LABELS[f.stop_reason] ?? f.stop_reason}</div>}
                      {f.error && <code className="small error-text">{f.error}</code>}
                    </td>
                    <td>
                      {f.traces.length === 0 ? (
                        <span className="muted small">–</span>
                      ) : (
                        f.traces.map((t) => (
                          <a key={t.attempt} href={t.url} target="_blank" rel="noreferrer noopener">
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
          <button type="button" className="button-quiet" onClick={onLoadOlder}>
            Show older failures
          </button>
        )}
      </Section>
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `ops-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section className="ops-section" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
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
    <div className={tone ? `tile ${tone}` : "tile"}>
      <p className="tile-value">
        {tone && (
          <span className="tile-mark" aria-hidden="true">
            {tone === "critical" ? "!" : "▲"}
          </span>
        )}
        {value}
      </p>
      <p className="tile-label">
        {label}
        {tone && <span className="visually-hidden"> ({tone})</span>}
      </p>
    </div>
  );
}

function Meter({ name, spent, budget }: { name: string; spent: number; budget: number }) {
  const share = budget > 0 ? spent / budget : 1;
  const tone = share >= 1 ? "critical" : share >= 0.8 ? "warning" : "";
  return (
    <li className={`meter ${tone}`}>
      <div className="meter-row">
        <span>{name}</span>
        <span className="num">
          {formatUsd(spent)} of {formatUsd(budget)}
          {tone === "critical" && " · limit reached"}
        </span>
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-label={`${name}: share of today's budget`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.round(share * 100))}
      >
        <div className="meter-fill" style={{ width: `${Math.min(100, share * 100)}%` }} />
      </div>
    </li>
  );
}

function ChartBox({ spec }: { spec: VegaLiteSpec }) {
  return (
    <Suspense fallback={<p className="muted small">Loading chart…</p>}>
      <Chart spec={spec} />
    </Suspense>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="muted ops-empty">{children}</p>;
}

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return seconds < 5 ? "just now" : `${formatDuration(seconds)} ago`;
}
