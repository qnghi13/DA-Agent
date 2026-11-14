import { describe, expect, it } from "vitest";
import type { ColumnProfile } from "./api";
import { columnDetail, share } from "./profile";

const column = (extra: Partial<ColumnProfile>): ColumnProfile => ({
  name: "c",
  type: "text",
  filled: 0,
  parsed: 0,
  failed_samples: [],
  distinct: null,
  min: null,
  max: null,
  mean: null,
  top: [],
  ...extra,
});

describe("share", () => {
  it("never rounds a part up to the whole", () => {
    expect(share(10, 10)).toBe("100%");
    expect(share(9999, 10000)).toBe("99.9%");
    expect(share(1, 3)).toBe("33.3%");
    expect(share(1, 100000)).toBe("<0.1%");
    expect(share(0, 10)).toBe("0%");
    expect(share(0, 0)).toBe("—");
  });
});

describe("columnDetail", () => {
  it("shows a date range", () => {
    expect(columnDetail(column({ type: "date", min: "2026-06-01", max: "2026-06-30" }))).toBe(
      "2026-06-01 → 2026-06-30",
    );
  });

  it("shows a number range and mean", () => {
    expect(columnDetail(column({ type: "money", min: 10, max: 22000, mean: 5000.25 }))).toBe(
      "10 – 22,000 · mean 5,000.25",
    );
  });

  it("shows the most frequent text values with counts", () => {
    const top = [
      { value: "completed", rows: 820 },
      { value: "Completed", rows: 12 },
    ];
    expect(columnDetail(column({ top }))).toBe("completed (820), Completed (12)");
  });

  it("does not list values when no value repeats, as in an id column", () => {
    const top = ["DH0001", "DH0002", "DH0003"].map((value) => ({ value, rows: 1 }));
    expect(columnDetail(column({ top }))).toBe("no value repeats, such as DH0001, DH0002, DH0003");
  });

  it("is empty when the build had no profile", () => {
    expect(columnDetail(column({ type: "number" }))).toBe("");
  });
});
