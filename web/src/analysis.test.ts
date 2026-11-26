import { describe, expect, it } from "vitest";
import type { QueryRecord, Run, RunResult } from "./api";
import {
  NOTE_CODES,
  analysisHeadline,
  changeSegments,
  drillFollowUp,
  evidenceAnalyses,
  noteText,
  whyChangedFollowUp,
} from "./analysis";
import { en } from "./i18n/en";
import { vi } from "./i18n/vi";

const CHANGE: QueryRecord = {
  sql: "SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL",
  columns: ["dimension", "segment", "before", "after", "change", "share_percent", "status"],
  rows: [
    ["(total)", "(all)", 6000, 4650, -1350, 100, "both"],
    ["branch", "Q1", 3000, 1550, -1450, 107.4, "both"],
    ["branch", "Q3", 3000, 3100, 100, -7.4, "both"],
    ["category", "Đồ uống", 4000, 2500, -1500, 111.1, "both"],
    ["category", "(blank)", 2000, 2150, 150, -11.1, "both"],
  ],
  analysis: {
    kind: "explain_change",
    arguments: {
      x: "day",
      y: "revenue",
      dimensions: ["branch", "category"],
      before: { start: "2026-09-01", end: "2026-09-30" },
      after: { start: "2026-10-01", end: "2026-10-31" },
    },
    summary: {
      before: { start: "2026-09-01", end: "2026-09-30", total: 6000 },
      after: { start: "2026-10-01", end: "2026-10-31", total: 4650 },
      change: -1350,
      change_percent: -22.5,
      flat: false,
      dimensions: ["category", "branch"],
      top: { dimension: "category", segment: "Đồ uống", change: -1500, share_percent: 111.1 },
    },
    notes: [],
  },
};

const MONTHLY: QueryRecord = {
  sql: "SELECT date_trunc('month', day) AS month, SUM(amount) AS revenue FROM orders GROUP BY 1",
  columns: ["month", "revenue"],
  rows: [
    ["2026-08-01", 6200],
    ["2026-09-01", 6000],
    ["2026-10-01", 4650],
  ],
};

function run(queries: QueryRecord[], evidence: number[], extra: Partial<RunResult> = {}): Run {
  return {
    id: "r1",
    dataset_id: "d1",
    question: "Doanh thu theo tháng",
    as_of: "2026-11-26",
    conversation_id: "c1",
    status: "succeeded",
    attempts: 1,
    error: null,
    traces: [],
    result: {
      answer: { kind: "number", value: 4650 },
      stop_reason: "answered",
      steps: [],
      iterations: 2,
      error: null,
      question_for_user: null,
      queries,
      ...extra,
    },
    explanations: evidence.map((index) => ({ query_index: index }) as never),
  };
}

describe("evidenceAnalyses", () => {
  it("picks only evidence records with an analysis", () => {
    const found = evidenceAnalyses(run([MONTHLY, CHANGE, CHANGE], [1, 1]));

    expect(found.map((a) => a.index)).toEqual([1]);
    expect(found[0].info.kind).toBe("explain_change");
    expect(evidenceAnalyses(run([MONTHLY, CHANGE], [0]))).toEqual([]);
  });
});

describe("changeSegments", () => {
  it("groups by dimension in table order and skips the total", () => {
    const groups = changeSegments(CHANGE);

    expect(groups.map((g) => g.dimension)).toEqual(["branch", "category"]);
    expect(groups[0].segments.map((s) => s.segment)).toEqual(["Q1", "Q3"]);
    expect(groups[0].segments[0]).toMatchObject({ change: -1450, share: 107.4 });
  });
});

describe("whyChangedFollowUp", () => {
  it("needs a time series and no explain_change yet", () => {
    expect(whyChangedFollowUp(run([MONTHLY], [0]), en)).not.toBeNull();
    expect(whyChangedFollowUp(run([MONTHLY, CHANGE], [0, 1]), en)).toBeNull();
    const byBranch: QueryRecord = { sql: "SELECT 1", columns: ["branch", "revenue"], rows: [["Q1", 1], ["Q3", 2]] };
    expect(whyChangedFollowUp(run([byBranch], [0]), en)).toBeNull();
    const single: QueryRecord = { ...MONTHLY, rows: [["2026-10-01", 4650]] };
    expect(whyChangedFollowUp(run([single], [0]), en)).toBeNull();
  });

  it("names the last two periods and forces explain_change", () => {
    const followUp = whyChangedFollowUp(run([MONTHLY], [0]), vi);

    expect(followUp).toEqual({
      kind: "explain_change",
      question: "Vì sao revenue thay đổi từ 2026-09 sang 2026-10?",
    });
    expect(whyChangedFollowUp(run([MONTHLY], [0]), en)?.question).toBe(
      "Why did revenue change from 2026-09 to 2026-10?",
    );
  });

  it("reads a chart drawn with make_chart too", () => {
    const chart = {
      data: { values: [{ m: "2026-09-01", v: 1 }, { m: "2026-10-01", v: 2 }] },
      encoding: { x: { field: "m", type: "temporal" }, y: { field: "v", type: "quantitative" } },
    };
    const steps = [{ tool: "make_chart", ok: true, chart }];
    const total: QueryRecord = { sql: "SELECT 1", columns: ["t"], rows: [[3]] };

    expect(whyChangedFollowUp(run([total], [0], { steps }), en)?.question).toBe(
      "Why did v change from 2026-09 to 2026-10?",
    );
  });

  it("takes each period once, in order, from a series per branch or a newest-first query", () => {
    const byBranch: QueryRecord = {
      sql: "SELECT 1",
      columns: ["month", "branch", "revenue"],
      rows: [
        ["2026-10-01", "Q1", 3],
        ["2026-10-01", "Q3", 4],
        ["2026-09-01", "Q1", 5],
        ["2026-09-01", "Q3", 6],
      ],
    };
    expect(whyChangedFollowUp(run([byBranch], [0]), en)?.question).toBe(
      "Why did revenue change from 2026-09 to 2026-10?",
    );
  });

  it("is not offered for an unanswered run", () => {
    const unanswered = run([MONTHLY], [0], { answer: { kind: "unanswerable" } });
    expect(whyChangedFollowUp(unanswered, en)).toBeNull();
  });
});

describe("drillFollowUp", () => {
  it("needs a top segment and a second dimension", () => {
    const followUp = drillFollowUp(CHANGE.analysis!, vi);

    expect(followUp?.kind).toBe("explain_change");
    expect(followUp?.question).toContain("Đồ uống");
    expect(followUp?.question).toContain("branch");
    const oneDimension = {
      ...CHANGE.analysis!,
      summary: { ...CHANGE.analysis!.summary, dimensions: ["category"] },
    };
    expect(drillFollowUp(oneDimension, vi)).toBeNull();
    const flat = { ...CHANGE.analysis!, summary: { ...CHANGE.analysis!.summary, top: null } };
    expect(drillFollowUp(flat, en)).toBeNull();
  });
});

describe("analysisHeadline", () => {
  it("says the change in English and Vietnamese", () => {
    expect(analysisHeadline(CHANGE.analysis!, en)).toBe(
      "revenue, 2026-10: 4,650, down 1,350 (22.5%) from 2026-09.",
    );
    expect(analysisHeadline(CHANGE.analysis!, vi)).toBe(
      "revenue, 2026-10: 4.650, giảm 1.350 (22,5%) so với 2026-09.",
    );
  });

  it("describes the other kinds", () => {
    const trend = {
      kind: "trend" as const,
      arguments: { x: "month", y: "revenue" },
      summary: { direction: "increasing", slope_per_period: 120.5, grain: "month" },
      notes: [],
    };
    expect(analysisHeadline(trend, en)).toBe("revenue is rising, by about 120.5 a month.");
    expect(analysisHeadline({ ...trend, summary: { direction: "no clear trend", grain: "month" } }, vi)).toBe(
      "revenue không có xu hướng rõ ràng.",
    );
  });
});

describe("notes", () => {
  it("every note code has a translation in both languages", () => {
    for (const code of NOTE_CODES) {
      const note = { code, params: { period: "2026-11", data_start: "2026-01-15", data_end: "2026-11-26", count: 2, grain: "month", column: "revenue", start: "2026-01-01", end: "2026-11-26" }, text: "fallback" };
      expect(noteText(note, en)).not.toBe("fallback");
      expect(noteText(note, vi)).not.toBe("fallback");
    }
  });

  it("falls back to the text for an unknown code", () => {
    expect(noteText({ code: "new_code", params: {}, text: "as written" }, vi)).toBe("as written");
  });
});
