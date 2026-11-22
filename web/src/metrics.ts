import type { MetricFacts } from "./api";
import { describeExpr, describeFilter } from "./explain";
import type { Messages } from "./i18n/en";

/** Metric definitions in the UI (docs/design/2026-11-15-metric-definitions.md). The API reads a
 * definition from an answer's SQL; this file words it and checks the names a person types. */

export const MAX_NAME = 80;
export const MAX_ALIASES = 10;

/** One sentence for a definition, such as "Total of Thành tiền, only rows where Trạng thái is
 * not “Hủy”". It uses the same words as "How it was calculated". */
export function metricSentence(facts: MetricFacts, m: Messages): string {
  const measure = describeExpr(facts.measure, m);
  const text = measure.charAt(0).toUpperCase() + measure.slice(1);
  const filters = facts.filters.map((filter) => describeFilter(filter, m)).join(m.explain.and);
  return filters ? m.metrics.onlyRowsWhere(text, filters) : text;
}

/** The name and other names a person typed, cleaned, or what is wrong with them. Other names are
 * separated by commas. */
export function readMetricName(
  name: string,
  aliasesText: string,
  m: Messages,
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
  if (!cleanName) return { error: m.metrics.nameMissing };
  if (cleanName.length > MAX_NAME) return { error: m.metrics.nameTooLong(MAX_NAME) };
  if (aliases.length > MAX_ALIASES) return { error: m.metrics.tooManyAliases(MAX_ALIASES) };
  if (aliases.some((alias) => alias.length > MAX_NAME)) {
    return { error: m.metrics.aliasTooLong(MAX_NAME) };
  }
  return { name: cleanName, aliases };
}
