import { describe, expect, it } from "vitest";
import type { ColumnProfile } from "./api";
import { en } from "./i18n/en";
import { vi } from "./i18n/vi";
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
    expect(share(10, 10, en)).toBe("100%");
    expect(share(9999, 10000, en)).toBe("99.9%");
    expect(share(1, 3, en)).toBe("33.3%");
    expect(share(1, 100000, en)).toBe("<0.1%");
    expect(share(0, 10, en)).toBe("0%");
    expect(share(0, 0, en)).toBe("—");
  });
});

describe("columnDetail", () => {
  it("shows a date range", () => {
    expect(columnDetail(column({ type: "date", min: "2026-06-01", max: "2026-06-30" }), en)).toBe(
      "2026-06-01 → 2026-06-30",
    );
  });

  it("shows a number range and mean", () => {
    expect(columnDetail(column({ type: "money", min: 10, max: 22000, mean: 5000.25 }), en)).toBe(
      "10 – 22,000 · mean 5,000.25",
    );
  });

  it("shows the most frequent text values with counts", () => {
    const top = [
      { value: "completed", rows: 820 },
      { value: "Completed", rows: 12 },
    ];
    expect(columnDetail(column({ top }), en)).toBe("completed (820), Completed (12)");
  });

  it("does not list values when no value repeats, as in an id column", () => {
    const top = ["DH0001", "DH0002", "DH0003"].map((value) => ({ value, rows: 1 }));
    expect(columnDetail(column({ top }), en)).toBe("no value repeats, such as DH0001, DH0002, DH0003");
  });

  it("is empty when the build had no profile", () => {
    expect(columnDetail(column({ type: "number" }), en)).toBe("");
  });
});

describe("in Vietnamese", () => {
  it("writes numbers the Vietnamese way", () => {
    const amount: ColumnProfile = {
      name: "thanh_tien", type: "money", filled: 10, parsed: 10, failed_samples: [], distinct: 10,
      min: 1000, max: 2500.5, mean: 1500.25, top: [],
    };
    expect(columnDetail(amount, vi)).toContain("2.500,5");
    expect(columnDetail(amount, vi)).not.toBe(columnDetail(amount, en));
    expect(share(1, 3, vi)).toBe("33,3%");
  });
});
