import { describe, expect, it } from "vitest";
import {
  alerts,
  formatDuration,
  formatPercent,
  formatUsd,
  groupOutcomes,
  groupSeries,
  type NowPanel,
} from "./ops";

const calm: NowPanel = {
  queued: 0,
  running: 0,
  oldest_queued_seconds: null,
  expired_leases: 0,
  last_worker_activity: "2026-11-09T10:00:00Z",
  datasets_awaiting_review: 0,
  datasets_processing: 0,
};
const at = Date.parse("2026-11-09T10:01:00Z");

describe("groupOutcomes", () => {
  it("folds run outcomes into five groups, in a fixed order, keeping empty ones out", () => {
    const groups = groupOutcomes({
      answered: 3,
      failed: 1,
      model_error: 2,
      unverified: 1,
      budget_exceeded: 1,
      in_progress: 1,
    });
    expect(groups).toEqual([
      { group: "Answered", count: 3 },
      { group: "Stopped", count: 2 },
      { group: "Error", count: 3 },
      { group: "In progress", count: 1 },
    ]);
  });
});

describe("groupSeries", () => {
  it("sums each bucket's outcomes by group", () => {
    const rows = groupSeries([
      { bucket: "2026-11-09T09:00:00Z", outcome: "failed", count: 1 },
      { bucket: "2026-11-09T09:00:00Z", outcome: "model_error", count: 2 },
      { bucket: "2026-11-09T10:00:00Z", outcome: "abstained", count: 1 },
    ]);
    expect(rows).toEqual([
      { bucket: "2026-11-09T09:00:00Z", group: "Error", count: 3 },
      { bucket: "2026-11-09T10:00:00Z", group: "Abstained", count: 1 },
    ]);
  });
});

describe("alerts", () => {
  it("is quiet when nothing waits", () => {
    expect(alerts(calm, at)).toEqual([]);
  });

  it("flags lost leases and a silent worker while jobs wait", () => {
    const now = {
      ...calm,
      queued: 2,
      expired_leases: 1,
      oldest_queued_seconds: 900,
      last_worker_activity: "2026-11-09T09:40:00Z",
    };
    expect(alerts(now, at).map((a) => a.level)).toEqual(["critical", "critical", "warning"]);
    expect(alerts(now, at)[1].text).toContain("21 min");
  });

  it("treats a worker that never ran as silent", () => {
    const now = { ...calm, queued: 1, last_worker_activity: null, oldest_queued_seconds: 10 };
    expect(alerts(now, at)).toEqual([
      { level: "critical", text: "Jobs are waiting and no worker has ever claimed one." },
    ]);
  });
});

describe("formatting", () => {
  it("formats durations, money, and percentages for reading at a glance", () => {
    expect(formatDuration(null)).toBe("–");
    expect(formatDuration(4.2)).toBe("4.2 s");
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(200)).toBe("3.3 min");
    expect(formatDuration(7200)).toBe("2.0 h");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.0012)).toBe("$0.0012");
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatPercent(null)).toBe("–");
    expect(formatPercent(0.333)).toBe("33%");
  });
});
