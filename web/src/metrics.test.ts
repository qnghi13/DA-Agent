import { describe, expect, it } from "vitest";
import type { MetricFacts } from "./api";
import { metricSentence, readMetricName } from "./metrics";

const REVENUE: MetricFacts = {
  measure: { kind: "aggregate", func: "sum", of: { kind: "column", column: "Thành tiền" }, condition: [] },
  filters: [
    { of: { kind: "column", column: "Trạng thái" }, op: "not_equals", values: ["Hủy"], match: "exact" },
  ],
};

describe("metricSentence", () => {
  it("words the measure and its row filters", () => {
    expect(metricSentence(REVENUE)).toBe("Total of Thành tiền, only rows where Trạng thái is not “Hủy”");
  });

  it("words a measure without filters", () => {
    expect(metricSentence({ measure: { kind: "aggregate", func: "count", of: null }, filters: [] })).toBe(
      "Number of rows",
    );
  });
});

describe("readMetricName", () => {
  it("trims the name and splits other names on commas, without blanks or repeats", () => {
    expect(readMetricName("  Doanh thu ", "doanh số, DT,, DT ")).toEqual({
      name: "Doanh thu",
      aliases: ["doanh số", "DT"],
    });
  });

  it("refuses an empty or long name, and too many or long other names", () => {
    expect(readMetricName("  ", "")).toEqual({ error: "Give the definition a name." });
    expect(readMetricName("x".repeat(81), "")).toEqual({ error: "A name has at most 80 characters." });
    expect(readMetricName("a", Array.from({ length: 11 }, (_, n) => `n${n}`).join(","))).toEqual({
      error: "At most 10 other names.",
    });
    expect(readMetricName("a", "y".repeat(81))).toEqual({ error: "Other names have at most 80 characters each." });
  });
});
