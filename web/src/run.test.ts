import { describe, expect, it } from "vitest";
import { formatAnswer, initialTrail, reduceTrail, toolLabel } from "./run";

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
  const en = "en-US";
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
});

describe("toolLabel", () => {
  it("names tools in plain words and falls back to the raw name", () => {
    expect(toolLabel("run_sql")).toBe("Queried the data");
    expect(toolLabel("something_new")).toBe("something_new");
  });
});
