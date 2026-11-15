import type { MetricFacts } from "./api";
import { describeExpr, describeFilter } from "./explain";

/** Metric definitions in the UI (docs/design/2026-11-15-metric-definitions.md). The API reads a
 * definition from an answer's SQL; this file words it and checks the names a person types. */

export const MAX_NAME = 80;
export const MAX_ALIASES = 10;

/** One sentence for a definition, such as "Total of Thành tiền, only rows where Trạng thái is
 * not “Hủy”". It uses the same words as "How it was calculated". */
export function metricSentence(facts: MetricFacts): string {
  const measure = describeExpr(facts.measure);
  const text = measure.charAt(0).toUpperCase() + measure.slice(1);
  const filters = facts.filters.map(describeFilter).join(" and ");
  return filters ? `${text}, only rows where ${filters}` : text;
}

/** The name and other names a person typed, cleaned, or what is wrong with them. Other names are
 * separated by commas. */
export function readMetricName(
  name: string,
  aliasesText: string,
): { name: string; aliases: string[] } | { error: string } {
  const cleanName = name.trim().replace(/\s+/g, " ");
  const aliases = [
    ...new Set(
      aliasesText
        .split(",")
        .map((alias) => alias.trim().replace(/\s+/g, " "))
        .filter(Boolean),
    ),
  ];
  if (!cleanName) return { error: "Give the definition a name." };
  if (cleanName.length > MAX_NAME) return { error: `A name has at most ${MAX_NAME} characters.` };
  if (aliases.length > MAX_ALIASES) return { error: `At most ${MAX_ALIASES} other names.` };
  if (aliases.some((alias) => alias.length > MAX_NAME)) {
    return { error: `Other names have at most ${MAX_NAME} characters each.` };
  }
  return { name: cleanName, aliases };
}
