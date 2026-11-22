import { describe, expect, it } from "vitest";
import type { Explanation, ExprFact, FilterFact } from "./api";
import { describeExpr, describeFilter, explanationLines, uniqueExplanations } from "./explain";

const col = (column: string, unit: string | null = null): ExprFact => ({ kind: "column", column, unit });
const agg = (func: string, of: ExprFact | null, extra: Partial<ExprFact> = {}): ExprFact => ({
  kind: "aggregate",
  func,
  of,
  distinct: false,
  condition: [],
  ...extra,
});
const filter = (of: ExprFact, op: string, values: unknown[], match = "exact"): FilterFact => ({
  of,
  op,
  values,
  match,
});
const explanation = (extra: Partial<Explanation>): Explanation => ({
  query_index: 0,
  tables: ["orders"],
  joins: [],
  measures: [],
  shown: [],
  filters: [],
  groups: [],
  having: [],
  order: [],
  limit: null,
  distinct_rows: false,
  unexplained: [],
  complete: true,
  ...extra,
});

describe("describeExpr", () => {
  it("names aggregates in plain words", () => {
    expect(describeExpr(agg("sum", col("Thành tiền")))).toBe("total of Thành tiền");
    expect(describeExpr(agg("count", null))).toBe("number of rows");
    expect(describeExpr(agg("count", col("Mã đơn"), { distinct: true }))).toBe(
      "number of different Mã đơn",
    );
    expect(describeExpr(agg("avg", col("Giá")))).toBe("average Giá");
  });

  it("keeps an aggregate's condition", () => {
    const done = filter(col("Trạng thái"), "equals", ["completed"]);
    expect(describeExpr(agg("sum", col("Thành tiền"), { condition: [done] }))).toBe(
      "total of Thành tiền where Trạng thái is “completed”",
    );
  });

  it("writes arithmetic with brackets around nested steps", () => {
    const share: ExprFact = {
      kind: "arithmetic",
      op: "multiply",
      left: { kind: "arithmetic", op: "divide", left: agg("sum", col("A")), right: agg("count", null) },
      right: { kind: "number", value: 100 },
    };
    expect(describeExpr(share)).toBe("(total of A ÷ number of rows) × 100");
  });

  it("brackets arithmetic inside an aggregate", () => {
    const amount: ExprFact = { kind: "arithmetic", op: "multiply", left: col("Số lượng"), right: col("Đơn giá") };
    expect(describeExpr(agg("sum", amount))).toBe("total of (Số lượng × Đơn giá)");
  });

  it("shows a time bucket", () => {
    expect(describeExpr(col("Ngày đặt", "month"))).toBe("Ngày đặt (month)");
    expect(describeExpr(col("Ngày đặt", "month_of_year"))).toBe("Ngày đặt (month of year)");
  });
});

describe("describeFilter", () => {
  it.each([
    [filter(col("S"), "in", ["a", "b"]), "S is “a” or “b”"],
    [filter(col("S"), "not_in", ["a", "b"]), "S is not “a” or “b”"],
    [filter(col("T"), "greater", [100]), "T is greater than 100"],
    [filter(col("T"), "between", [10, 20]), "T is between 10 and 20"],
    [filter(col("S"), "contains", ["may", "máy"], "ignore_case_and_accents"),
      "S contains “may” or “máy” (ignoring case and accents)"],
    [filter(col("S"), "is_blank", []), "S is empty"],
    [filter(col("Paid"), "is_true", []), "Paid is yes"],
    [filter(col("T"), "greater", [col("Paid")]), "T is greater than Paid"],
  ])("%#", (fact, text) => {
    expect(describeFilter(fact)).toBe(text);
  });

  it("writes periods as dates people read", () => {
    expect(describeFilter(filter(col("D"), "period", ["2025-06-01", "2025-06-30"]))).toBe(
      "D from 1 Jun 2025 to 30 Jun 2025",
    );
    expect(describeFilter(filter(col("D"), "period", ["2025-06-01", null]))).toBe(
      "D on or after 1 Jun 2025",
    );
    expect(describeFilter(filter(col("D"), "period", [null, "2025-06-30"]))).toBe(
      "D on or before 30 Jun 2025",
    );
  });
});

describe("explanationLines", () => {
  it("covers every part of a query, in reading order", () => {
    const revenue = { ...agg("sum", col("Thành tiền")), name: "revenue" };
    const lines = explanationLines(
      explanation({
        tables: ["Đơn hàng"],
        joins: [{ table: "Khách hàng", kind: "left", on: [["customer_id", "id"]] }],
        measures: [revenue],
        filters: [filter(col("Trạng thái"), "in", ["completed", "Completed"])],
        groups: [col("Ngày đặt", "month")],
        having: [filter(agg("count", null), "greater", [10])],
        order: [{ by: revenue, descending: true }],
        limit: 5,
      }),
    );

    expect(lines).toEqual([
      { label: "Data", text: "Đơn hàng, matched with Khách hàng on customer_id = id (rows without a match kept)" },
      { label: "Calculated", text: "total of Thành tiền" },
      { label: "Only rows where", text: "Trạng thái is “completed” or “Completed”" },
      { label: "Grouped by", text: "Ngày đặt (month)" },
      { label: "Only groups where", text: "number of rows is greater than 10" },
      { label: "Shown", text: "top 5 by total of Thành tiền" },
    ]);
  });

  it("lists plain columns when nothing is calculated", () => {
    const lines = explanationLines(explanation({ shown: [col("Trạng thái")], distinct_rows: true }));

    expect(lines).toContainEqual({ label: "Shows", text: "Trạng thái, each combination once" });
  });

  it("orders without a limit and limits without an order", () => {
    const sorted = explanationLines(explanation({ order: [{ by: col("Giá"), descending: false }] }));
    const limited = explanationLines(explanation({ limit: 10 }));

    expect(sorted).toContainEqual({ label: "Shown", text: "sorted by Giá, lowest first" });
    expect(limited).toContainEqual({ label: "Shown", text: "the first 10 rows" });
  });
});

describe("uniqueExplanations", () => {
  const completed = filter(col("Trạng thái"), "equals", ["completed"]);
  const count = agg("count", col("Mã đơn"), { distinct: true });

  it("keeps one of the queries that read the same in plain words", () => {
    const first = explanation({ query_index: 0, measures: [count], filters: [completed] });
    const again = explanation({ query_index: 1, measures: [count], filters: [completed] });

    expect(uniqueExplanations([first, again])).toEqual([first]);
  });

  it("keeps queries that differ, in their order", () => {
    const completedCount = explanation({ query_index: 0, measures: [count], filters: [completed] });
    const allCount = explanation({ query_index: 1, measures: [count] });

    expect(uniqueExplanations([completedCount, allCount])).toEqual([completedCount, allCount]);
  });

  it("keeps a query that cannot be put fully into words next to one that can", () => {
    const whole = explanation({ query_index: 0, measures: [count] });
    const partial = explanation({ query_index: 1, measures: [count], complete: false });

    expect(uniqueExplanations([whole, partial])).toEqual([whole, partial]);
  });
});
