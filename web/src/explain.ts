import type { Explanation, ExprFact, FilterFact } from "./api";
import type { Messages } from "./i18n/en";

/** Sentences for "How it was calculated" (docs/design/2026-11-15-plain-explanation.md). The API
 * returns facts read from the SQL; this file puts them together, and the words and their order
 * come from the UI language's dictionary (web/src/i18n). */

function numberFormat(m: Messages): Intl.NumberFormat {
  return new Intl.NumberFormat(m.locale, { maximumFractionDigits: 6 });
}

function date(iso: string, m: Messages): string {
  const [year, month, day] = iso.split("-").map(Number);
  return m.explain.date(day, month, year);
}

function isExpr(value: unknown): value is ExprFact {
  return typeof value === "object" && value !== null && "kind" in value;
}

function value(item: unknown, m: Messages): string {
  if (isExpr(item)) return describeExpr(item, m);
  if (typeof item === "number") return numberFormat(m).format(item);
  if (typeof item === "string") return `“${item}”`;
  return String(item);
}

/** Values joined with "or": “a” or “b” or “c”. */
function either(values: unknown[], m: Messages): string {
  return values.map((item) => value(item, m)).join(m.explain.or);
}

const OPERATORS: Record<string, string> = { add: "+", subtract: "−", multiply: "×", divide: "÷" };

export function describeExpr(fact: ExprFact, m: Messages): string {
  const words = m.explain;
  switch (fact.kind) {
    case "column":
      return fact.unit ? `${fact.column} (${words.units[fact.unit] ?? fact.unit})` : (fact.column ?? "");
    case "number":
      return numberFormat(m).format(fact.value ?? 0);
    case "all":
      return words.allColumns;
    case "arithmetic": {
      const side = (part?: ExprFact) =>
        part ? (part.kind === "arithmetic" ? `(${describeExpr(part, m)})` : describeExpr(part, m)) : "";
      return `${side(fact.left)} ${OPERATORS[fact.op ?? ""] ?? fact.op} ${side(fact.right)}`;
    }
    case "aggregate": {
      const inner = fact.of ? describeExpr(fact.of, m) : null;
      const of = inner && fact.of?.kind === "arithmetic" ? `(${inner})` : inner;
      const distinct = Boolean(fact.distinct);
      const text =
        fact.func === "count"
          ? words.count(of, distinct)
          : words.aggregate(fact.func ?? "", of ?? words.rows, distinct);
      const condition = fact.condition ?? [];
      if (!condition.length) return text;
      return words.where(text, condition.map((item) => describeFilter(item, m)).join(words.and));
    }
  }
}

export function describeFilter(fact: FilterFact, m: Messages): string {
  const words = m.explain;
  const subject = describeExpr(fact.of, m);
  const match = words.matches[fact.match] ?? "";
  const [first, second] = fact.values;
  switch (fact.op) {
    case "period":
      if (first && second) return words.period(subject, date(String(first), m), date(String(second), m));
      if (first) return words.onOrAfter(subject, date(String(first), m));
      return words.onOrBefore(subject, date(String(second), m));
    case "in":
      return `${words.is(subject, either(fact.values, m))}${match}`;
    case "not_in":
      return `${words.isNot(subject, either(fact.values, m))}${match}`;
    case "between":
      return words.between(subject, value(first, m), value(second, m));
    case "is_blank":
      return words.empty(subject);
    case "is_not_blank":
      return words.notEmpty(subject);
    case "is_true":
      return words.isYes(subject);
    case "is_false":
      return words.isNo(subject);
    default:
      return `${subject} ${words.comparisons[fact.op] ?? fact.op} ${either(fact.values, m)}${match}`;
  }
}

function source(explanation: Explanation, m: Messages): string {
  const words = m.explain;
  const joins = explanation.joins.map((join) => {
    const on = join.on.map((pair) => pair.join(" = ")).join(words.and);
    const kept = join.kind === "left" ? words.keptUnmatched : "";
    return `${words.matchedWith(join.table, on)}${kept}`;
  });
  return [explanation.tables.join(", "), ...joins].filter(Boolean).join(", ");
}

function shown(explanation: Explanation, m: Messages): string | null {
  const { order, limit } = explanation;
  const by = order.map((item) => describeExpr(item.by, m)).join(", ");
  if (order.length && limit !== null) return m.explain.top(order[0].descending, limit, by);
  if (order.length) return m.explain.sorted(by, order[0].descending);
  if (limit !== null) return m.explain.firstRows(limit);
  return null;
}

/** One labelled line per part of the query, in the order a person would read them. */
export function explanationLines(explanation: Explanation, m: Messages): { label: string; text: string }[] {
  const labels = m.explain.labels;
  const lines: { label: string; text: string }[] = [];
  const add = (label: string, text: string | null) => {
    if (text) lines.push({ label, text });
  };
  const expr = (item: ExprFact) => describeExpr(item, m);
  const filter = (item: FilterFact) => describeFilter(item, m);
  add(labels.data, source(explanation, m));
  add(labels.calculated, explanation.measures.map(expr).join("; "));
  if (explanation.groups.length === 0) {
    const columns = explanation.shown.map(expr).join(", ");
    add(labels.shows, columns && explanation.distinct_rows ? m.explain.eachCombinationOnce(columns) : columns);
  }
  add(labels.rowsWhere, explanation.filters.map(filter).join("; "));
  add(labels.groupedBy, explanation.groups.map(expr).join(", "));
  add(labels.groupsWhere, explanation.having.map(filter).join("; "));
  add(labels.shown, shown(explanation, m));
  return lines;
}

/** The queries an answer ran, without repeats: a model sometimes runs the same query again to
 * check it, and two copies of one calculation only add noise. Queries count as the same when
 * they read the same in plain words; the first one is kept. */
export function uniqueExplanations(explanations: Explanation[], m: Messages): Explanation[] {
  const seen = new Set<string>();
  return explanations.filter((explanation) => {
    const key = JSON.stringify([explanation.complete, explanationLines(explanation, m)]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
