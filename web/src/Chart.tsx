import { useEffect, useRef, useState } from "react";
import embed, { type VisualizationSpec } from "vega-embed";
import { expressionInterpreter } from "vega-interpreter";
import type { Config } from "vega-lite";
import type { VegaLiteSpec } from "./api";

// Categorical slots, validated with the dataviz skill's validate_palette.js against the card
// surface in each mode. Light slots 3–5 sit under 3:1, so charts keep tooltips and the
// query table below them.
const LIGHT = {
  series: ["#4744c9", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
  text: "#1b1f2a",
  muted: "#5d6374",
  grid: "#e3e6ec",
};
const DARK = {
  series: ["#7270f0", "#d95926", "#199e70", "#c98500", "#d55181"],
  text: "#e7e9f0",
  muted: "#a3a9b8",
  grid: "#2e3446",
};

function config(dark: boolean): Config {
  const t = dark ? DARK : LIGHT;
  const font = "'IBM Plex Sans', system-ui, sans-serif";
  return {
    background: "transparent",
    font,
    view: { stroke: null },
    range: { category: t.series },
    mark: { color: t.series[0] },
    bar: { cornerRadiusEnd: 4, continuousBandSize: 18, discreteBandSize: { band: 0.7 } },
    line: { strokeWidth: 2 },
    point: { size: 64, filled: true },
    arc: { stroke: dark ? "#1e2230" : "#ffffff", strokeWidth: 2 },
    title: { color: t.text, fontSize: 14, fontWeight: 600, anchor: "start", offset: 12 },
    axis: {
      labelColor: t.muted,
      titleColor: t.muted,
      titleFontWeight: 500,
      labelFontSize: 12,
      titleFontSize: 12,
      gridColor: t.grid,
      domainColor: t.grid,
      tickColor: t.grid,
    },
    axisX: { grid: false },
    legend: { labelColor: t.text, titleColor: t.muted, labelFontSize: 12, titleFontSize: 12 },
  };
}

function prefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** A Vega-Lite spec built by the backend from a query. No spec expressions are compiled: the
 * interpreter evaluates them, matching the report's CSP-safe setup. */
export default function Chart({ spec }: { spec: VegaLiteSpec }) {
  const holder = useRef<HTMLDivElement>(null);
  const [dark, setDark] = useState(prefersDark);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    const update = () => setDark(query.matches);
    query?.addEventListener("change", update);
    return () => query?.removeEventListener("change", update);
  }, []);

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
      config: config(dark),
    })
      .then((result) => {
        if (cancelled) result.finalize();
        else finalize = () => result.finalize();
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      finalize?.();
    };
  }, [spec, dark]);

  if (failed) return <p className="muted">This chart could not be drawn.</p>;
  return <div className="chart" ref={holder} />;
}
