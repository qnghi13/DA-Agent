// Run progress and answers: turning SSE events into the evidence trail, and answers into text.
// Event shapes come from node_events in src/da_agent/agent/graph.py.

import type { RunResult, RunStatus } from "./api";
import type { Messages } from "./i18n/en";
import type { SseEvent } from "./sse";
import type { Tone } from "./status";

export type TrailStep =
  | { id: string; kind: "plan"; steps: string[] }
  | {
      id: string;
      kind: "tool";
      tool: string;
      ok: boolean;
      sql: string | null;
      rowCount: number | null;
      error: string | null;
      chart: boolean;
    }
  | { id: string; kind: "verify"; passed: boolean; problems: string[] };

export interface Trail {
  steps: TrailStep[];
  seen: string[];
  turns: number;
  status: "running" | "succeeded" | "failed" | "cancelled";
  stopReason?: string;
  question?: string;
}

export function initialTrail(): Trail {
  return { steps: [], seen: [], turns: 0, status: "running" };
}

type Data = Record<string, unknown>;

export function reduceTrail(trail: Trail, event: SseEvent): Trail {
  if (event.id && trail.seen.includes(event.id)) return trail;
  let data: Data;
  try {
    data = JSON.parse(event.data) as Data;
  } catch {
    return trail;
  }
  const id = event.id ?? `local-${trail.seen.length}`;
  const next: Trail = { ...trail, seen: event.id ? [...trail.seen, event.id] : trail.seen };
  switch (event.event) {
    case "plan":
      return { ...next, steps: [...next.steps, { id, kind: "plan", steps: strings(data.steps) }] };
    case "turn":
      return { ...next, turns: next.turns + 1 };
    case "tool":
      return {
        ...next,
        steps: [
          ...next.steps,
          {
            id,
            kind: "tool",
            tool: String(data.tool ?? "tool"),
            ok: Boolean(data.ok),
            sql: typeof data.sql === "string" ? data.sql : null,
            rowCount: typeof data.row_count === "number" ? data.row_count : null,
            error: typeof data.error === "string" ? data.error : null,
            chart: Boolean(data.chart),
          },
        ],
      };
    case "verify":
      return {
        ...next,
        steps: [
          ...next.steps,
          { id, kind: "verify", passed: Boolean(data.passed), problems: strings(data.problems) },
        ],
      };
    case "done":
      return { ...next, stopReason: typeof data.stop_reason === "string" ? data.stop_reason : undefined };
    case "question":
      return { ...next, question: typeof data.question === "string" ? data.question : undefined };
    case "end":
      return {
        ...next,
        status:
          data.status === "succeeded" || data.status === "cancelled" ? data.status : "failed",
      };
    default:
      return next;
  }
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/** What a running agent is doing now, in a few words, from its latest step. */
export function activity(trail: Trail, m: Messages): string {
  const last = trail.steps.at(-1);
  if (!last) return m.run.planning;
  if (last.kind === "verify") return m.run.checking;
  const queries = trail.steps.filter((step) => step.kind === "tool" && step.tool === "run_sql");
  if (last.kind === "tool" && queries.length > 0) return m.run.querying(queries.length);
  return m.run.reading;
}

/**
 * The line a finished answer collapses to. "Verified" only when the graph answered, which it does
 * only after verification passes. Runs carry no duration or cost for members, so it shows neither.
 */
export function answerSummary(
  status: RunStatus,
  result: RunResult | null,
  m: Messages,
): { label: string; tone: Tone; detail: string | null } {
  const words = m.run;
  if (status === "cancelled") {
    const detail = result?.queries.length ? words.queries(result.queries.length) : null;
    return { label: words.stopped, tone: "neutral", detail };
  }
  if (status === "failed" || !result) return { label: words.failed, tone: "danger", detail: null };
  const detail = result.queries.length ? words.queries(result.queries.length) : null;
  if (result.stop_reason === "answered") return { label: words.verified, tone: "success", detail };
  if (result.stop_reason === "needs_clarification") {
    return { label: words.needsDetail, tone: "warning", detail };
  }
  return { label: words.noAnswer, tone: "warning", detail };
}

export function toolLabel(tool: string, m: Messages): string {
  return m.run.tools[tool] ?? tool;
}

/** Whether a run may still change: the UI follows it and offers to stop it. */
export function isRunActive(status: RunStatus): boolean {
  return status === "queued" || status === "running";
}

/**
 * Whether the composer's button stops a run instead of sending: only while the latest run of the
 * conversation being written in is active. A new conversation can always send.
 */
export function composerStops(
  latest: { id: string; conversation_id: string | null } | undefined,
  conversation: string | null,
  statuses: Record<string, RunStatus>,
): boolean {
  if (!latest || conversation === null || latest.conversation_id !== conversation) return false;
  const status = statuses[latest.id];
  return status !== undefined && isRunActive(status);
}

/** Exporting or rerunning a run's queries needs a finished run; the API refuses the others. */
export function canActOnQueries(status: RunStatus): boolean {
  return status === "succeeded";
}

export function stopReasonText(reason: string | undefined, m: Messages): string | undefined {
  return reason ? (m.run.stopReasons[reason] ?? reason) : undefined;
}

export interface Answer {
  kind: string;
  value?: unknown;
  reason?: string | null;
}

export type FormattedAnswer =
  | { text: string }
  | { list: string[] }
  | { table: { columns: string[]; rows: string[][] } };

function formatScalar(value: unknown, m: Messages): string {
  if (typeof value === "number") {
    return value.toLocaleString(m.locale, { maximumFractionDigits: 2 });
  }
  if (typeof value === "boolean") return value ? m.run.yes : m.run.no;
  if (value === null || value === undefined) return "";
  return String(value);
}

export function formatAnswer(answer: Answer, m: Messages): FormattedAnswer {
  switch (answer.kind) {
    case "unanswerable":
      return { text: m.run.noAnswer };
    case "percent":
      return { text: `${formatScalar(answer.value, m)}%` };
    case "list":
      return { list: Array.isArray(answer.value) ? answer.value.map((v) => formatScalar(v, m)) : [] };
    case "table": {
      const table = answer.value as { columns?: string[]; rows?: unknown[][] } | undefined;
      return {
        table: {
          columns: table?.columns ?? [],
          rows: (table?.rows ?? []).map((row) => row.map((cell) => formatScalar(cell, m))),
        },
      };
    }
    default:
      return { text: formatScalar(answer.value, m) };
  }
}
