import { describe, expect, it } from "vitest";
import type { ColumnProfile, ColumnSpec, Dataset, TableSpec } from "./api";
import { GENERIC_QUESTIONS, suggestQuestions } from "./suggestions";

const column = (name: string, type: ColumnSpec["type"], extra: Partial<ColumnSpec> = {}): ColumnSpec => ({
  source: name,
  name,
  type,
  ...extra,
});

const profile = (name: string, distinct: number, filled = 200): ColumnProfile => ({
  name,
  type: "text",
  filled,
  parsed: filled,
  failed_samples: [],
  distinct,
  min: null,
  max: null,
  mean: null,
  top: [],
});

const ORDERS: TableSpec = {
  source: "orders.csv",
  name: "orders",
  columns: [
    column("ma_don", "text", { source: "Mã đơn" }),
    column("ngay", "date", { source: "Ngày đặt" }),
    column("thanh_tien", "money", { source: "Thành tiền" }),
    column("so_luong", "quantity", { source: "Số lượng" }),
    column("ten_sp", "text", { source: "Tên sản phẩm" }),
    column("trang_thai", "text", { source: "Trạng thái" }),
    column("sdt", "text", { source: "Số điện thoại", sensitive: true }),
  ],
};
const ORDER_PROFILE = [
  profile("ma_don", 200),
  profile("ten_sp", 40),
  profile("trang_thai", 3),
];

function dataset(tables: TableSpec[], profiles: Record<string, ColumnProfile[] | undefined> = {}, rows: Record<string, number> = {}): Dataset {
  return {
    id: "d",
    name: "Shop",
    description: "",
    status: "ready",
    spec: { tables },
    summary: {
      tables: tables.map((t) => ({ name: t.name, rows: rows[t.name] ?? 200, row: "", columns: t.columns.length, profile: profiles[t.name] })),
    },
    error: null,
    files: [],
    documents: 0,
  };
}

describe("suggestQuestions", () => {
  it("asks about the data's own columns, by the names in the file", () => {
    expect(suggestQuestions(dataset([ORDERS], { orders: ORDER_PROFILE }))).toEqual([
      "What is the total Số lượng by month?",
      "Top 5 Tên sản phẩm by total Số lượng",
      "How many records are there for each Trạng thái?",
      "Which month had the highest total Số lượng?",
      "What date range does Ngày đặt cover?",
    ]);
  });

  it("without a profile, asks only what column types support", () => {
    expect(suggestQuestions(dataset([ORDERS]))).toEqual([
      "What is the total Số lượng by month?",
      "Which month had the highest total Số lượng?",
      "What date range does Ngày đặt cover?",
    ]);
  });

  it("sums a quantity before money, which may be a unit price", () => {
    const priced: TableSpec = {
      ...ORDERS,
      columns: ORDERS.columns.filter((c) => c.name !== "thanh_tien").concat(column("gia", "money", { source: "Đơn giá" })),
    };

    expect(suggestQuestions(dataset([priced])).join(" ")).not.toContain("Đơn giá");
  });

  it("sums money when there is no quantity", () => {
    const money: TableSpec = { ...ORDERS, columns: ORDERS.columns.filter((c) => c.type !== "quantity") };

    expect(suggestQuestions(dataset([money]))[0]).toBe("What is the total Thành tiền by month?");
  });

  it("reads a quantity typed as an integer by its name, and never sums a unit price", () => {
    const shop: TableSpec = {
      source: "orders",
      name: "orders",
      columns: [
        column("ngay", "date", { source: "Ngày" }),
        column("sl", "integer", { source: "Số lượng" }),
        column("gia", "money", { source: "Đơn giá" }),
      ],
    };
    const prices: TableSpec = { ...shop, columns: shop.columns.filter((c) => c.name !== "sl") };
    const totals: TableSpec = { ...prices, columns: [...prices.columns, column("tong", "money", { source: "Tổng giá trị" })] };

    expect(suggestQuestions(dataset([shop]))[0]).toBe("What is the total Số lượng by month?");
    expect(suggestQuestions(dataset([prices]))).toEqual(["What date range does Ngày cover?"]);
    expect(suggestQuestions(dataset([totals]))[0]).toBe("What is the total Tổng giá trị by month?");
  });

  it("never names a sensitive column", () => {
    const hidden: TableSpec = {
      ...ORDERS,
      columns: ORDERS.columns.map((c) =>
        c.name === "so_luong" || c.name === "trang_thai" ? { ...c, sensitive: true } : c,
      ),
    };

    const questions = suggestQuestions(dataset([hidden], { orders: ORDER_PROFILE })).join(" ");

    expect(questions).toContain("total Thành tiền");
    expect(questions).not.toContain("Số lượng");
    expect(questions).not.toContain("Trạng thái");
    expect(questions).not.toContain("Số điện thoại");
  });

  it("does not rank by a column whose values never repeat", () => {
    const ids: TableSpec = {
      ...ORDERS,
      columns: ORDERS.columns.filter((c) => c.name !== "ten_sp"),
    };

    const questions = suggestQuestions(dataset([ids], { orders: ORDER_PROFILE })).join(" ");

    expect(questions).not.toContain("Mã đơn");
    expect(questions).not.toContain("Top 5");
  });

  it("asks for a total when there is no date", () => {
    const undated: TableSpec = { ...ORDERS, columns: ORDERS.columns.filter((c) => c.type !== "date") };

    expect(suggestQuestions(dataset([undated]))).toEqual(["What is the total Số lượng?"]);
  });

  it("picks the table with dates and amounts, then the one with more rows", () => {
    const lookup: TableSpec = { source: "codes", name: "codes", columns: [column("code", "text")] };
    const archive: TableSpec = {
      source: "archive",
      name: "archive",
      columns: [column("d", "date", { source: "Archived on" }), column("v", "money", { source: "Old value" })],
    };

    const questions = suggestQuestions(
      dataset([lookup, archive, ORDERS], {}, { codes: 9000, archive: 50, orders: 200 }),
    );

    expect(questions[0]).toBe("What is the total Số lượng by month?");
  });

  it("falls back to general questions when nothing fits", () => {
    const plain: TableSpec = { source: "t", name: "t", columns: [column("n", "integer")] };

    expect(suggestQuestions(dataset([plain]))).toEqual(GENERIC_QUESTIONS);
    expect(suggestQuestions({ ...dataset([]), spec: null })).toEqual(GENERIC_QUESTIONS);
  });
});
