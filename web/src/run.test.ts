import { describe, expect, it } from "vitest";
import {
  activity,
  answerSummary,
  formatAnswer,
  initialTrail,
  reduceTrail,
  isLongText,
  runCharts,
  toolLabel,
  stopReasonText,
} from "./run";
import { en } from "./i18n/en";
import { vi } from "./i18n/vi";

const sse = (id: string | undefined, event: string, data: unknown) => ({
  id,
  event,
  data: JSON.stringify(data),
});

describe("reduceTrail", () => {
  it("builds steps from plan, tool, and verify events, then finishes on end", () => {
    let trail = initialTrail();
    trail = reduceTrail(trail, sse("1", "plan", { steps: ["Sum June revenue"] }));
    trail = reduceTrail(trail, sse("2", "turn", { iteration: 1, tools: ["run_sql"] }));
    trail = reduceTrail(
      trail,
      sse("3", "tool", { tool: "run_sql", ok: true, sql: "SELECT 1", row_count: 1, error: null, chart: false }),
    );
    trail = reduceTrail(trail, sse("4", "verify", { passed: false, problems: ["period"] }));
    trail = reduceTrail(trail, sse("5", "done", { stop_reason: "answered" }));
    expect(trail.status).toBe("running");
    trail = reduceTrail(trail, sse(undefined, "end", { status: "succeeded" }));

    expect(trail.steps).toEqual([
      { id: "1", kind: "plan", steps: ["Sum June revenue"] },
      { id: "3", kind: "tool", tool: "run_sql", ok: true, sql: "SELECT 1", rowCount: 1, error: null, chart: false },
      { id: "4", kind: "verify", passed: false, problems: ["period"] },
    ]);
    expect(trail.turns).toBe(1);
    expect(trail.stopReason).toBe("answered");
    expect(trail.status).toBe("succeeded");
  });

  it("ignores an event id it has already seen", () => {
    const event = sse("9", "tool", { tool: "describe_dataset", ok: true });
    const once = reduceTrail(initialTrail(), event);
    expect(reduceTrail(once, event)).toBe(once);
  });

  it("keeps a clarifying question and treats a failed job as failed", () => {
    let trail = reduceTrail(initialTrail(), sse("1", "question", { question: "Which year?" }));
    trail = reduceTrail(trail, sse(undefined, "end", { status: "failed" }));
    expect(trail.question).toBe("Which year?");
    expect(trail.status).toBe("failed");
  });

  it("skips events whose data is not JSON", () => {
    const trail = initialTrail();
    expect(reduceTrail(trail, { id: "1", event: "plan", data: "{oops" })).toEqual(trail);
  });
});

describe("formatAnswer", () => {
  it("groups numbers and marks percents", () => {
    expect(formatAnswer({ kind: "number", value: 1500000 }, en)).toEqual({ text: "1,500,000" });
    expect(formatAnswer({ kind: "number", value: 12.3456 }, en)).toEqual({ text: "12.35" });
    expect(formatAnswer({ kind: "percent", value: 12.4 }, en)).toEqual({ text: "12.4%" });
  });

  it("writes booleans, dates, and text as they are meant", () => {
    expect(formatAnswer({ kind: "boolean", value: true }, en)).toEqual({ text: "Yes" });
    expect(formatAnswer({ kind: "date", value: "2026-06" }, en)).toEqual({ text: "2026-06" });
    expect(formatAnswer({ kind: "text", value: "Ao thun" }, en)).toEqual({ text: "Ao thun" });
  });

  it("returns lists and tables as structures", () => {
    expect(formatAnswer({ kind: "list", value: ["a", 2] }, en)).toEqual({ list: ["a", "2"] });
    expect(
      formatAnswer({ kind: "table", value: { columns: ["p", "n"], rows: [["x", 1000]] } }, en),
    ).toEqual({ table: { columns: ["p", "n"], rows: [["x", "1,000"]] } });
  });

  it("says when there is no answer", () => {
    expect(formatAnswer({ kind: "unanswerable", reason: "no cost data" }, en)).toEqual({
      text: "No answer",
    });
  });

  it("drops a midnight time, as DATE_TRUNC returns it, but keeps a real time of day", () => {
    const rows = [["2026-04-01 00:00:00", 9064000], ["2026-05-01T00:00:00", 7998000]];
    expect(formatAnswer({ kind: "table", value: { columns: ["month", "revenue"], rows } }, en))
      .toEqual({
        table: { columns: ["month", "revenue"], rows: [["2026-04-01", "9,064,000"], ["2026-05-01", "7,998,000"]] },
      });
    expect(formatAnswer({ kind: "date", value: "2026-06-01 00:00:00" }, en)).toEqual({
      text: "2026-06-01",
    });
    expect(formatAnswer({ kind: "text", value: "2026-06-01 09:30:00" }, en)).toEqual({
      text: "2026-06-01 09:30:00",
    });
  });
});

describe("runCharts", () => {
  const spec = (title: string, revenue: number) => ({
    title,
    mark: { type: "bar" },
    data: { values: [{ channel: "web", revenue }] },
  });

  it("draws each chart once, under its last title, in the order drawn", () => {
    const steps = [
      { tool: "make_chart", ok: true, chart: spec("Revenue", 1) },
      { tool: "run_sql", ok: true, chart: null },
      { tool: "make_chart", ok: true, chart: spec("Orders", 2) },
      { tool: "make_chart", ok: true, chart: spec("Revenue by channel", 1) },
    ];

    expect(runCharts(steps)).toEqual([spec("Orders", 2), spec("Revenue by channel", 1)]);
  });

  it("is empty without charts", () => {
    expect(runCharts([{ tool: "run_sql", ok: true }])).toEqual([]);
  });
});

describe("toolLabel", () => {
  it("names tools in plain words and falls back to the raw name", () => {
    expect(toolLabel("run_sql", en)).toBe("Queried the data");
    expect(toolLabel("something_new", en)).toBe("something_new");
  });
});

describe("answerSummary", () => {
  const result = (stop_reason: string, queries = 0) => ({
    answer: null,
    stop_reason,
    steps: [],
    iterations: 1,
    error: null,
    question_for_user: null,
    queries: Array.from({ length: queries }, () => ({ sql: "SELECT 1", columns: [], rows: [] })),
  });

  it("says verified only when the graph answered, and counts the queries", () => {
    expect(answerSummary("succeeded", result("answered", 3), en)).toEqual({
      label: "Verified",
      tone: "success",
      detail: "3 queries",
    });
    expect(answerSummary("succeeded", result("answered", 1), en).detail).toBe("1 query");
  });

  it("marks clarifications, abstentions, and failed jobs", () => {
    expect(answerSummary("succeeded", result("needs_clarification"), en).label).toBe("Needs detail");
    expect(answerSummary("succeeded", result("unverified", 2), en)).toEqual({
      label: "No answer",
      tone: "warning",
      detail: "2 queries",
    });
    expect(answerSummary("failed", null, en)).toEqual({ label: "Failed", tone: "danger", detail: null });
  });
});

describe("activity", () => {
  it("names what the agent is doing from the latest step", () => {
    const start = initialTrail();
    expect(activity(start, en)).toBe("Planning");
    const planned = reduceTrail(start, sse("1", "plan", { steps: ["a"] }));
    expect(activity(planned, en)).toBe("Reading the data");
    const queried = reduceTrail(
      reduceTrail(planned, sse("2", "tool", { tool: "run_sql", ok: true })),
      sse("3", "tool", { tool: "run_sql", ok: true }),
    );
    expect(activity(queried, en)).toBe("Querying the data · 2 queries");
    const checked = reduceTrail(queried, sse("4", "verify", { passed: false, problems: [] }));
    expect(activity(checked, en)).toBe("Checking the answer");
  });
});

describe("in Vietnamese", () => {
  it("describes the same step in each language", () => {
    let trail = initialTrail();
    trail = reduceTrail(trail, sse("1", "tool", { tool: "run_sql", args: {} }));
    expect(activity(trail, vi)).not.toBe(activity(trail, en));
    expect(activity(initialTrail(), vi)).not.toBe(activity(initialTrail(), en));
  });

  it("explains why a run stopped", () => {
    expect(stopReasonText("cancelled", vi)).toBe(vi.run.stopReasons.cancelled);
    expect(stopReasonText("cancelled", vi)).not.toBe(stopReasonText("cancelled", en));
    expect(stopReasonText("something_new", vi)).toBe("something_new");
  });

  it("formats numbers the Vietnamese way", () => {
    expect(formatAnswer({ kind: "number", value: 1234.5 }, vi)).toEqual({ text: "1.234,5" });
    expect(formatAnswer({ kind: "boolean", value: true }, vi)).toEqual({ text: "Có" });
  });
});

describe("isLongText", () => {
  it("keeps short values as a headline and long text as reading text", () => {
    expect(isLongText({ kind: "number", value: 8714000 }, en)).toBe(false);
    expect(isLongText({ kind: "text", value: "GrabFood" }, en)).toBe(false);
    const sentence = "Revenue rose from 7,998,000 in May to 8,714,000 in June, mostly in GrabFood.";
    expect(isLongText({ kind: "text", value: sentence }, en)).toBe(true);
  });
});
