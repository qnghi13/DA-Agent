// Run progress and answers: turning SSE events into the evidence trail, and answers into text.
// Event shapes come from node_events in src/da_agent/agent/graph.py.

import type { SseEvent } from "./sse";

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
  status: "running" | "succeeded" | "failed";
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
      return { ...next, status: data.status === "succeeded" ? "succeeded" : "failed" };
    default:
      return next;
  }
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

const TOOL_LABELS: Record<string, string> = {
  describe_dataset: "Read the table descriptions",
  run_sql: "Queried the data",
  make_chart: "Drew a chart",
  search_rules: "Looked up the business rules",
  submit_answer: "Proposed an answer",
};

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool;
}

const STOP_REASONS: Record<string, string> = {
  answered: "Answered",
  needs_clarification: "The agent needs more detail to answer.",
  iteration_limit: "Stopped after too many steps without an answer.",
  budget_exceeded: "Stopped: this question reached its spending limit.",
  llm_error: "The language model could not be reached.",
  invalid_tool_call: "The model kept making invalid tool calls.",
  no_tool_call: "The model stopped without answering.",
};

export function stopReasonText(reason: string | undefined): string | undefined {
  return reason ? (STOP_REASONS[reason] ?? reason) : undefined;
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

function formatScalar(value: unknown, locale?: string): string {
  if (typeof value === "number") {
    return value.toLocaleString(locale, { maximumFractionDigits: 2 });
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined) return "";
  return String(value);
}

export function formatAnswer(answer: Answer, locale?: string): FormattedAnswer {
  switch (answer.kind) {
    case "unanswerable":
      return { text: "No answer" };
    case "percent":
      return { text: `${formatScalar(answer.value, locale)}%` };
    case "list":
      return { list: Array.isArray(answer.value) ? answer.value.map((v) => formatScalar(v, locale)) : [] };
    case "table": {
      const table = answer.value as { columns?: string[]; rows?: unknown[][] } | undefined;
      return {
        table: {
          columns: table?.columns ?? [],
          rows: (table?.rows ?? []).map((row) => row.map((cell) => formatScalar(cell, locale))),
        },
      };
    }
    default:
      return { text: formatScalar(answer.value, locale) };
  }
}
