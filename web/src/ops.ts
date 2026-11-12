// The operator dashboard's data (docs/adr/0026): types for /v1/ops, and the small pure functions
// the page uses to group, judge, and format it.

export type Window = "24h" | "7d" | "30d";

export interface NowPanel {
  queued: number;
  running: number;
  oldest_queued_seconds: number | null;
  expired_leases: number;
  last_worker_activity: string | null;
  datasets_awaiting_review: number;
  datasets_processing: number;
}

export interface ModelRow {
  provider: string;
  model: string;
  calls: number;
  errors: number;
  latency_p50: number | null;
  latency_p95: number | null;
  input_tokens: number;
  output_tokens: number;
  cached_share: number | null;
  cost_usd: number;
}

export interface OpsSummary {
  window: Window;
  bucket: "hour" | "day";
  generated_at: string;
  daily_budget_default_usd: number;
  now: NowPanel;
  runs: {
    total: number;
    outcomes: Record<string, number>;
    answer_rate: number | null;
    abstain_rate: number | null;
    retried: number;
    series: { bucket: string; outcome: string; count: number }[];
  };
  latency: {
    queue_wait_p50: number | null;
    queue_wait_p95: number | null;
    run_p50: number | null;
    run_p95: number | null;
    series: { bucket: string; run_p50: number | null; run_p95: number | null }[];
  };
  models: ModelRow[];
  model_errors: {
    total: number;
    by_kind: Record<string, number>;
    series: { bucket: string; kind: string; count: number }[];
  };
  cost: {
    total_usd: number;
    per_answered_run_usd: number | null;
    series: { bucket: string; cost_usd: number }[];
    by_workspace: { workspace_id: string; name: string; cost_usd: number; runs: number }[];
    today: { workspace_id: string; name: string; spent_usd: number; budget_usd: number }[];
  };
  quality: {
    verify_checks: number;
    verify_failed: number;
    runs_repaired: number;
    tool_calls: number;
    tool_errors: number;
    ingest_jobs: number;
    ingest_failed: number;
    tool_error_kinds: Record<string, number>;
    verify_kinds: Record<string, number>;
    runs_saved: number;
  };
  security: { auth_failed: number; sign_ins: number; series: { bucket: string; count: number }[] };
}

export interface FailureRow {
  id: string;
  kind: string;
  workspace_id: string;
  workspace_name: string;
  status: string;
  attempts: number;
  stop_reason: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  traces: { attempt: number; url: string }[];
}

/** Outcome groups in stacking order; the colours in OpsDashboard follow the same order. */
export const GROUPS = ["Answered", "Abstained", "Stopped", "Error", "In progress"] as const;
export type Group = (typeof GROUPS)[number];

const GROUP_OF: Record<string, Group> = {
  answered: "Answered",
  abstained: "Abstained",
  unverified: "Stopped",
  budget_exceeded: "Stopped",
  iteration_limit: "Stopped",
  needs_clarification: "Stopped",
  other: "Stopped",
  model_error: "Error",
  failed: "Error",
  in_progress: "In progress",
};

/** What each raw outcome means, for the outcome table. */
export const OUTCOME_LABELS: Record<string, string> = {
  answered: "Answered",
  abstained: "Abstained: the data could not answer it",
  unverified: "Stopped: the answer failed verification",
  budget_exceeded: "Stopped: the run budget ran out",
  iteration_limit: "Stopped: too many steps",
  needs_clarification: "Waiting for the user to clarify",
  model_error: "Error from the model provider",
  failed: "Job failed after its retries",
  in_progress: "Queued or running",
  other: "Other stop",
};

/** What a run's stop reason means, for the failure list. */
export const STOP_LABELS: Record<string, string> = {
  ...OUTCOME_LABELS,
  unreadable_values: "Stopped: the answer used values the build could not read",
  llm_error: "Error from the model provider",
  invalid_tool_call: "The model sent a tool call it could not repair",
  no_tool_call: "The model stopped without answering",
};

export function groupOf(outcome: string): Group {
  return GROUP_OF[outcome] ?? "Stopped";
}

export function groupOutcomes(outcomes: Record<string, number>): { group: Group; count: number }[] {
  const totals = new Map<Group, number>();
  for (const [outcome, count] of Object.entries(outcomes)) {
    const group = groupOf(outcome);
    totals.set(group, (totals.get(group) ?? 0) + count);
  }
  return GROUPS.filter((g) => totals.get(g)).map((group) => ({ group, count: totals.get(group)! }));
}

export function groupSeries(
  series: { bucket: string; outcome: string; count: number }[],
): { bucket: string; group: Group; count: number }[] {
  const totals = new Map<string, number>();
  for (const row of series) {
    const key = `${row.bucket}|${groupOf(row.outcome)}`;
    totals.set(key, (totals.get(key) ?? 0) + row.count);
  }
  return [...totals.entries()]
    .map(([key, count]) => {
      const [bucket, group] = key.split("|");
      return { bucket, group: group as Group, count };
    })
    .sort(
      (a, b) =>
        a.bucket.localeCompare(b.bucket) || GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group),
    );
}

export interface Alert {
  level: "critical" | "warning";
  text: string;
}

const SILENT_WORKER_SECONDS = 300;
const SLOW_QUEUE_SECONDS = 300;

/** Problems the operator should act on now, most serious first. */
export function alerts(now: NowPanel, nowMs: number): Alert[] {
  const found: Alert[] = [];
  if (now.expired_leases > 0) {
    found.push({
      level: "critical",
      text: `${now.expired_leases} running job(s) lost their worker; they restart when a worker claims them.`,
    });
  }
  if (now.queued > 0) {
    if (now.last_worker_activity === null) {
      found.push({ level: "critical", text: "Jobs are waiting and no worker has ever claimed one." });
    } else {
      const silent = (nowMs - Date.parse(now.last_worker_activity)) / 1000;
      if (silent > SILENT_WORKER_SECONDS) {
        found.push({
          level: "critical",
          text: `Jobs are waiting and no worker has worked for ${formatDuration(silent)}. Check the worker.`,
        });
      }
    }
    if ((now.oldest_queued_seconds ?? 0) > SLOW_QUEUE_SECONDS) {
      found.push({
        level: "warning",
        text: `The oldest job has waited ${formatDuration(now.oldest_queued_seconds)}.`,
      });
    }
  }
  return found;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return "–";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 120) return `${Math.round(seconds)} s`;
  if (seconds < 600) return `${(seconds / 60).toFixed(1)} min`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export function formatUsd(usd: number | null): string {
  if (usd === null) return "–";
  if (usd > 0 && usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatPercent(share: number | null): string {
  return share === null ? "–" : `${Math.round(share * 100)}%`;
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}
