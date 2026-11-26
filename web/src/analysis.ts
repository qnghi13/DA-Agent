import type { AnalysisInfo, AnalysisKind, AnalysisNote, DateRange, QueryRecord, Run } from "./api";
import type { Messages } from "./i18n/en";

/** Fixed analyses in the chat (docs/design/2026-11-26-analysis-tools.md, ADR 0051). Code, not a
 * model, writes every sentence here from the numbers the analysis returned, so the words match
 * the numbers. A "why" is where the change happened, never its cause. */

/** Every note code the analyses write; each has a sentence in every language. */
export const NOTE_CODES = [
  "unfinished_first",
  "unfinished_last",
  "missing_periods",
  "missing_values",
  "interpolated",
  "rows_without_value",
  "coverage_unknown",
  "flat_total",
  "before_zero",
  "missing_dates",
] as const;

const TOTAL = "(total)";
const OTHER = "(other)";
const BLANK = "(blank)";
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

export interface FollowUp {
  question: string;
  kind: AnalysisKind;
}

export interface EvidenceAnalysis {
  index: number;
  record: QueryRecord;
  info: AnalysisInfo;
}

export interface Segment {
  segment: string;
  before: number;
  after: number;
  change: number;
  share: number | null;
  status: string;
}

/** The analyses behind the answer, from the queries its explanations name, each once. */
export function evidenceAnalyses(run: Run): EvidenceAnalysis[] {
  const queries = run.result?.queries ?? [];
  const indices = [...new Set((run.explanations ?? []).map((e) => e.query_index))];
  return indices.flatMap((index) => {
    const record = queries[index];
    return record?.analysis ? [{ index, record, info: record.analysis }] : [];
  });
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0);
}

/** An explain_change table by dimension, in its rank order, without the total row. */
export function changeSegments(record: QueryRecord): { dimension: string; segments: Segment[] }[] {
  const groups = new Map<string, Segment[]>();
  for (const row of record.rows) {
    const dimension = String(row[0]);
    if (dimension === TOTAL) continue;
    const segments = groups.get(dimension) ?? [];
    segments.push({
      segment: String(row[1]),
      before: asNumber(row[2]),
      after: asNumber(row[3]),
      change: asNumber(row[4]),
      share: typeof row[5] === "number" ? row[5] : null,
      status: String(row[6] ?? ""),
    });
    groups.set(dimension, segments);
  }
  return [...groups].map(([dimension, segments]) => ({ dimension, segments }));
}

export function formatNumber(value: unknown, m: Messages): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return value.toLocaleString(m.locale, { maximumFractionDigits: 2 });
}

export function segmentLabel(segment: string, m: Messages): string {
  if (segment === OTHER) return m.analysis.other;
  if (segment === BLANK) return m.analysis.blank;
  return segment;
}

function lastDayOfMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** A range as people say it: a whole month as 2026-10, one day as that day. */
export function rangeLabel(range: DateRange | null | undefined, m: Messages): string {
  if (!range) return "";
  const { start, end } = range;
  if (start === end) return start;
  const [year, month, day] = start.split("-").map(Number);
  const wholeMonth =
    day === 1 &&
    end.slice(0, 7) === start.slice(0, 7) &&
    Number(end.slice(8, 10)) === lastDayOfMonth(year, month);
  return wholeMonth ? start.slice(0, 7) : m.analysis.range(start, end);
}

function summaryRange(info: AnalysisInfo, key: "before" | "after"): DateRange | null {
  const value = info.summary[key] as Partial<DateRange> | undefined;
  return value?.start && value?.end ? { start: value.start, end: value.end } : null;
}

function percent(value: unknown, m: Messages): string | null {
  if (typeof value !== "number") return null;
  return `${Math.abs(value).toLocaleString(m.locale, { maximumFractionDigits: 1 })}%`;
}

/** One sentence that says what the analysis found, in the UI language. */
export function analysisHeadline(info: AnalysisInfo, m: Messages): string {
  const measure = info.arguments.y ?? "";
  const s = info.summary;
  switch (info.kind) {
    case "explain_change": {
      const after = s.after as { total?: number } | undefined;
      const change = asNumber(s.change);
      return m.analysis.changeHeadline({
        measure,
        after: rangeLabel(summaryRange(info, "after"), m),
        before: rangeLabel(summaryRange(info, "before"), m),
        total: formatNumber(after?.total, m),
        direction: s.flat ? "flat" : change < 0 ? "down" : "up",
        amount: formatNumber(Math.abs(change), m),
        percent: percent(s.change_percent, m),
      });
    }
    case "trend": {
      const grain = String(s.grain ?? "");
      return m.analysis.trendHeadline({
        measure,
        direction: String(s.direction ?? ""),
        slope: formatNumber(Math.abs(asNumber(s.slope_per_period)), m),
        grain: m.analysis.grains[grain] ?? grain,
      });
    }
    case "forecast": {
      return m.analysis.forecastHeadline({
        measure,
        horizon: String(s.horizon ?? ""),
        grain: m.analysis.grains[String(s.grain ?? "")] ?? String(s.grain ?? ""),
      });
    }
    case "correlation":
      return m.analysis.correlationHeadline({
        x: info.arguments.x ?? "",
        y: measure,
        strength: String(s.strength ?? ""),
        positive: asNumber(s.pearson) >= 0,
        r: formatNumber(s.pearson, m),
      });
    case "outliers":
      return m.analysis.outliersHeadline({ count: formatNumber(s.outliers, m), measure });
    default:
      return "";
  }
}

/** A note in the UI language; a code this web does not know shows the text the model read. */
export function noteText(note: AnalysisNote, m: Messages): string {
  const translate = m.analysis.notes[note.code];
  return translate ? translate(note.params) : note.text;
}

function dateLabel(value: string, monthly: boolean): string {
  const day = value.slice(0, 10);
  return monthly ? day.slice(0, 7) : day;
}

interface Series {
  measure: string;
  periods: string[];
}

function chartSeries(steps: NonNullable<Run["result"]>["steps"]): Series | null {
  for (const step of [...steps].reverse()) {
    const chart = step.chart as
      | { data?: { values?: Record<string, unknown>[] }; encoding?: Record<string, { field?: string; type?: string; timeUnit?: string }> }
      | null
      | undefined;
    const x = chart?.encoding?.x;
    const y = chart?.encoding?.y;
    const values = chart?.data?.values ?? [];
    if (!x?.field || !y?.field || (x.type !== "temporal" && !x.timeUnit)) continue;
    const periods = values.map((v) => v[x.field ?? ""]).filter((v): v is string => typeof v === "string");
    if (periods.length >= 2) return { measure: y.field, periods };
  }
  return null;
}

function querySeries(record: QueryRecord): Series | null {
  if (record.analysis || record.rows.length < 2) return null;
  const periods = record.rows.map((row) => row[0]);
  if (!periods.every((p) => typeof p === "string" && ISO_DATE.test(p))) return null;
  const numeric = record.columns.findIndex((_, i) => i > 0 && typeof record.rows[0][i] === "number");
  if (numeric < 0) return null;
  return { measure: record.columns[numeric], periods: periods as string[] };
}

/** "Why did it change?" under an answer over time: the last two periods it shows, explained by
 * code. None when the answer is not a series, or when it already explains a change. */
export function whyChangedFollowUp(run: Run, m: Messages): FollowUp | null {
  const result = run.result;
  if (run.status !== "succeeded" || !result?.answer || result.answer.kind === "unanswerable") {
    return null;
  }
  if (evidenceAnalyses(run).some((a) => a.info.kind === "explain_change")) return null;
  const evidence = [...new Set((run.explanations ?? []).map((e) => e.query_index))];
  const series =
    chartSeries(result.steps) ??
    evidence.map((i) => result.queries[i]).flatMap((q) => (q ? [querySeries(q)] : []))
      .find((s): s is Series => s !== null) ??
    null;
  if (!series) return null;
  // Each period once, oldest first: a series per branch repeats periods, and a query may list
  // the newest first.
  const periods = [...new Set(series.periods.map((p) => p.slice(0, 10)))].sort();
  if (periods.length < 2) return null;
  const monthly = periods.every((p) => p.slice(8, 10) === "01");
  const [previous, last] = periods.slice(-2).map((p) => dateLabel(p, monthly));
  return { kind: "explain_change", question: m.analysis.whyQuestion(series.measure, previous, last) };
}

/** "See Q1 by product": the same change, inside the segment that carried most of it, split by
 * the next column. None when the change has no leading segment or only one column. */
export function drillFollowUp(info: AnalysisInfo, m: Messages): FollowUp | null {
  if (info.kind !== "explain_change") return null;
  const top = info.summary.top as { dimension?: string; segment?: string } | null | undefined;
  const ranked = (info.summary.dimensions as string[] | undefined) ?? [];
  const next = ranked.find((d) => d !== top?.dimension);
  if (!top?.dimension || !top.segment || !next) return null;
  return {
    kind: "explain_change",
    question: m.analysis.drillQuestion({
      measure: info.arguments.y ?? "",
      segment: segmentLabel(top.segment, m),
      dimension: top.dimension,
      before: rangeLabel(summaryRange(info, "before"), m),
      after: rangeLabel(summaryRange(info, "after"), m),
      next,
    }),
  };
}
