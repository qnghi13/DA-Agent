import { Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import embed, { type Result, type VisualizationSpec } from "vega-embed";
import { expressionInterpreter } from "vega-interpreter";
import type { Config } from "vega-lite";
import type { VegaLiteSpec } from "./api";
import { Button } from "./components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./components/ui/dropdown-menu";
import { chartFilename } from "./export";
import { useDark } from "./hooks/use-dark";

// Categorical slots in the dataviz skill's validated order, checked with its validate_palette.js
// against the card surface of each mode (#ffffff, #111111): all gates pass. Light slots 3–5 sit
// under 3:1, so charts keep tooltips and the evidence panel's Data tab holds every value. One
// series uses slot 1, the UI's only accent; text and axes use the neutral tokens.
const LIGHT = {
  series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  surface: "#ffffff",
  text: "#0a0a0a",
  muted: "#666666",
  grid: "#ebebeb",
};
const DARK = {
  series: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  surface: "#111111",
  text: "#ededed",
  muted: "#a1a1a1",
  grid: "#262626",
};

export function chartConfig(dark: boolean): Config {
  const t = dark ? DARK : LIGHT;
  const font = "'Geist Variable', ui-sans-serif, system-ui, sans-serif";
  return {
    background: "transparent",
    font,
    padding: { top: 4, right: 4, bottom: 0, left: 0 },
    view: { stroke: null },
    range: { category: t.series },
    mark: { color: t.series[0], tooltip: true },
    bar: {
      cornerRadiusEnd: 4,
      continuousBandSize: 24,
      discreteBandSize: { band: 0.6 },
      stroke: t.surface,
      strokeWidth: 2,
    },
    line: { strokeWidth: 2 },
    point: { size: 64, filled: true, stroke: t.surface, strokeWidth: 2 },
    arc: { stroke: t.surface, strokeWidth: 2 },
    title: { color: t.text, fontSize: 13, fontWeight: 500, anchor: "start", offset: 14 },
    axis: {
      labelColor: t.muted,
      titleColor: t.muted,
      titleFontWeight: 400,
      labelFontSize: 11,
      titleFontSize: 11,
      gridColor: t.grid,
      gridDash: [2, 3],
      domain: false,
      ticks: false,
      labelPadding: 6,
    },
    axisX: { grid: false, domain: true, domainColor: t.grid },
    legend: {
      labelColor: t.text,
      titleColor: t.muted,
      labelFontSize: 11,
      titleFontSize: 11,
      symbolType: "circle",
      symbolSize: 64,
    },
  };
}

/** A Vega-Lite spec built by the backend from a query. No spec expressions are compiled: the
 * interpreter evaluates them, matching the report's CSP-safe setup. */
export default function Chart({ spec }: { spec: VegaLiteSpec }) {
  const holder = useRef<HTMLDivElement>(null);
  const dark = useDark();
  const [failed, setFailed] = useState(false);
  const view = useRef<Result["view"] | null>(null);

  useEffect(() => {
    if (!holder.current) return;
    let finalize: (() => void) | undefined;
    let cancelled = false;
    const full = { ...spec, width: "container", autosize: { type: "fit", contains: "padding" } };
    embed(holder.current, full as VisualizationSpec, {
      actions: false,
      renderer: "svg",
      ast: true,
      expr: expressionInterpreter,
      config: chartConfig(dark),
      tooltip: { theme: dark ? "dark" : "light" },
    })
      .then((result) => {
        if (cancelled) result.finalize();
        else {
          view.current = result.view;
          finalize = () => {
            view.current = null;
            result.finalize();
          };
        }
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      finalize?.();
    };
  }, [spec, dark]);

  if (failed) return <p className="text-muted-foreground text-sm">This chart could not be drawn.</p>;
  return (
    <div className="group relative w-full">
      <div className="chart w-full" ref={holder} />
      <ChartDownload
        view={view}
        title={typeof spec.title === "string" ? spec.title : undefined}
        surface={(dark ? DARK : LIGHT).surface}
      />
    </div>
  );
}

/** PNG or SVG of the chart as drawn. The chart holds every row it plots: make_chart refuses a
 * truncated result (docs/design/2026-11-14-result-export.md). */
function ChartDownload({
  view,
  title,
  surface,
}: {
  view: React.RefObject<Result["view"] | null>;
  title: string | undefined;
  surface: string;
}) {
  async function save(format: "png" | "svg") {
    const current = view.current;
    if (!current) return;
    // On screen the card is the background; a saved image needs its own, or dark-mode text
    // disappears on a white slide.
    const background = current.background();
    current.background(surface);
    let url: string;
    try {
      url = await current.toImageURL(format, format === "png" ? 2 : 1);
    } finally {
      current.background(background);
    }
    const link = document.createElement("a");
    link.href = url;
    link.download = chartFilename(title, format);
    link.click();
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Download chart"
          className="absolute top-0 right-0 size-7 opacity-60 group-hover:opacity-100 focus-visible:opacity-100"
        >
          <Download />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void save("png")}>Download PNG</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void save("svg")}>Download SVG</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
