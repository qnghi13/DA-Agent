import type { ColumnProfile } from "./api";

/** Formatting for the data overview (docs/design/2026-11-14-data-overview.md). */

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** A part of a whole as a percentage that never rounds a missing cell away. */
export function share(part: number, whole: number): string {
  if (whole <= 0) return "—";
  if (part >= whole) return "100%";
  if (part <= 0) return "0%";
  const tenths = Math.floor((part / whole) * 1000) / 10;
  return tenths === 0 ? "<0.1%" : `${tenths}%`;
}

/** The range of a date or number column, or the most frequent values of a text one. */
export function columnDetail(column: ColumnProfile): string {
  if (column.type === "date" || column.type === "datetime") {
    return column.min !== null && column.max !== null ? `${column.min} → ${column.max}` : "";
  }
  if (typeof column.min === "number" && typeof column.max === "number") {
    const mean = typeof column.mean === "number" ? ` · mean ${number.format(column.mean)}` : "";
    return `${number.format(column.min)} – ${number.format(column.max)}${mean}`;
  }
  // Values are listed most frequent first, so a first value seen once means none repeats.
  if (column.top.length > 0 && column.top[0].rows === 1) {
    return `no value repeats, such as ${column.top
      .slice(0, 3)
      .map((top) => top.value)
      .join(", ")}`;
  }
  return column.top.map((top) => `${top.value} (${number.format(top.rows)})`).join(", ");
}
