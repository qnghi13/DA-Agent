import { ChevronRight, Info, TrendingDown, TrendingUp } from "lucide-react";
import { lazy, Suspense } from "react";
import type { AnalysisInfo, QueryRecord } from "@/api";
import {
  type FollowUp,
  analysisHeadline,
  changeSegments,
  drillFollowUp,
  formatNumber,
  noteText,
  segmentLabel,
} from "@/analysis";
import { useT } from "@/i18n/context";
import type { Messages } from "@/i18n/en";
import { Button } from "@/components/ui/button";

// Vega is large; load it only when a chart is on screen.
const Chart = lazy(() => import("@/Chart"));

/** A fixed analysis under the answer (ADR 0051): what code found, its chart, where a change
 * happened, and what the data cannot say. Every number here came back from the analysis. */
export function AnalysisCard({
  record,
  info,
  onFollowUp,
}: {
  record: QueryRecord;
  info: AnalysisInfo;
  onFollowUp?: (followUp: FollowUp) => void;
}) {
  const m = useT();
  const drill = onFollowUp ? drillFollowUp(info, m) : null;
  const top = info.summary.top as { segment?: string; dimension?: string } | null | undefined;
  return (
    <section className="flex flex-col gap-3 rounded-xl border p-4">
      <p className="text-[15px] font-medium">{analysisHeadline(info, m)}</p>
      {info.chart && (
        <Suspense fallback={<div className="bg-muted h-48 animate-pulse rounded-lg" />}>
          <Chart spec={info.chart} />
        </Suspense>
      )}
      {info.kind === "explain_change" && <ChangeSegments record={record} flat={Boolean(info.summary.flat)} m={m} />}
      {info.notes.length > 0 && (
        <ul className="text-muted-foreground flex flex-col gap-1 text-sm">
          {info.notes.map((note, index) => (
            <li key={index} className="flex gap-2">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {noteText(note, m)}
            </li>
          ))}
        </ul>
      )}
      {info.kind === "explain_change" && (
        <p className="bg-muted rounded-lg px-3 py-2 text-sm">{m.analysis.notCause}</p>
      )}
      <p className="text-muted-foreground text-xs">
        <span className="font-medium">{m.analysis.method}:</span> {m.analysis.methods[info.kind]}
      </p>
      {drill && top?.segment && top.dimension && (
        <div>
          <Button variant="outline" size="sm" onClick={() => onFollowUp?.(drill)}>
            {m.analysis.drill(segmentLabel(top.segment, m), String(nextDimension(info) ?? ""))}
            <ChevronRight />
          </Button>
        </div>
      )}
    </section>
  );
}

function nextDimension(info: AnalysisInfo): string | undefined {
  const top = info.summary.top as { dimension?: string } | null | undefined;
  return ((info.summary.dimensions as string[] | undefined) ?? []).find((d) => d !== top?.dimension);
}

function ChangeSegments({ record, flat, m }: { record: QueryRecord; flat: boolean; m: Messages }) {
  const [first, ...others] = changeSegments(record);
  if (!first) return null;
  return (
    <div className="flex flex-col gap-2 text-sm">
      {flat && <p className="text-muted-foreground">{m.analysis.flat}</p>}
      <SegmentList title={m.analysis.whereChanged} dimension={first.dimension} segments={first.segments} m={m} />
      {others.length > 0 && (
        <details className="group">
          <summary className="text-muted-foreground flex cursor-pointer list-none items-center gap-1 select-none">
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden />
            {m.analysis.otherColumns}
          </summary>
          <div className="mt-2 flex flex-col gap-2">
            {others.map((group) => (
              <SegmentList key={group.dimension} dimension={group.dimension} segments={group.segments} m={m} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function SegmentList({
  title,
  dimension,
  segments,
  m,
}: {
  title?: string;
  dimension: string;
  segments: ReturnType<typeof changeSegments>[number]["segments"];
  m: Messages;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-muted-foreground">
        {title ? `${title} · ` : ""}
        <span className="font-medium">{dimension}</span>
      </p>
      <ul className="flex flex-col gap-1">
        {segments.map((segment) => (
          <li key={segment.segment} className="flex flex-wrap items-center gap-x-2 tabular-nums">
            {segment.change < 0 ? (
              <TrendingDown className="text-danger size-3.5" aria-hidden />
            ) : (
              <TrendingUp className="text-success size-3.5" aria-hidden />
            )}
            <span className="font-medium">{segmentLabel(segment.segment, m)}</span>
            <span>
              {segment.change > 0 ? "+" : ""}
              {formatNumber(segment.change, m)}
            </span>
            {segment.share !== null && (
              <span className="text-muted-foreground">
                {m.analysis.share(formatNumber(segment.share, m))}
              </span>
            )}
            {m.analysis.status[segment.status] && (
              <span className="text-muted-foreground rounded-full border px-1.5 text-[11px]">
                {m.analysis.status[segment.status]}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
