import type { Explanation, ExprFact, FilterFact } from "./api";

/** Sentences for "How it was calculated" (docs/design/2026-11-15-plain-explanation.md). The API
 * returns facts read from the SQL; this file is the only place that words them, so translating
 * the UI changes only this file. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const UNITS: Record<string, string> = {
  day: "day",
  month: "month",
  year: "year",
  quarter: "quarter",
  hour: "hour of day",
  weekday: "day of week",
  month_of_year: "month of year",
};

const AGGREGATES: Record<string, string> = {
  sum: "total of",
  avg: "average",
  min: "lowest",
  max: "highest",
  median: "median",
};

const OPERATORS: Record<string, string> = { add: "+", subtract: "−", multiply: "×", divide: "÷" };

const COMPARISONS: Record<string, string> = {
  equals: "is",
  not_equals: "is not",
  greater: "is greater than",
  at_least: "is at least",
  less: "is less than",
  at_most: "is at most",
  starts_with: "starts with",
  ends_with: "ends with",
  contains: "contains",
};

const MATCHES: Record<string, string> = {
  ignore_case: " (ignoring case)",
  ignore_accents: " (ignoring accents)",
  ignore_case_and_accents: " (ignoring case and accents)",
};

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });

function date(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

function isExpr(value: unknown): value is ExprFact {
  return typeof value === "object" && value !== null && "kind" in value;
}

function value(item: unknown): string {
  if (isExpr(item)) return describeExpr(item);
  if (typeof item === "number") return number.format(item);
  if (typeof item === "string") return `“${item}”`;
  return String(item);
}

/** Values joined with "or": “a” or “b” or “c”. */
function either(values: unknown[]): string {
  return values.map(value).join(" or ");
}

export function describeExpr(fact: ExprFact): string {
  switch (fact.kind) {
    case "column":
      return fact.unit ? `${fact.column} (${UNITS[fact.unit] ?? fact.unit})` : (fact.column ?? "");
    case "number":
      return number.format(fact.value ?? 0);
    case "all":
      return "all columns";
    case "arithmetic": {
      const side = (part?: ExprFact) =>
        part ? (part.kind === "arithmetic" ? `(${describeExpr(part)})` : describeExpr(part)) : "";
      return `${side(fact.left)} ${OPERATORS[fact.op ?? ""] ?? fact.op} ${side(fact.right)}`;
    }
    case "aggregate": {
      const inner = fact.of ? describeExpr(fact.of) : null;
      const of = inner && fact.of?.kind === "arithmetic" ? `(${inner})` : inner;
      let text: string;
      if (fact.func === "count") {
        text = of ? `number of ${fact.distinct ? "different " : ""}${of}` : "number of rows";
      } else {
        text = `${AGGREGATES[fact.func ?? ""] ?? fact.func} ${fact.distinct ? "different " : ""}${of ?? "rows"}`;
      }
      const condition = fact.condition ?? [];
      return condition.length ? `${text} where ${condition.map(describeFilter).join(" and ")}` : text;
    }
  }
}

export function describeFilter(fact: FilterFact): string {
  const subject = describeExpr(fact.of);
  const match = MATCHES[fact.match] ?? "";
  const [first, second] = fact.values;
  switch (fact.op) {
    case "period":
      if (first && second) return `${subject} from ${date(String(first))} to ${date(String(second))}`;
      if (first) return `${subject} on or after ${date(String(first))}`;
      return `${subject} on or before ${date(String(second))}`;
    case "in":
      return `${subject} is ${either(fact.values)}${match}`;
    case "not_in":
      return `${subject} is not ${either(fact.values)}${match}`;
    case "between":
      return `${subject} is between ${value(first)} and ${value(second)}`;
    case "is_blank":
      return `${subject} is empty`;
    case "is_not_blank":
      return `${subject} is not empty`;
    case "is_true":
      return `${subject} is yes`;
    case "is_false":
      return `${subject} is no`;
    default:
      return `${subject} ${COMPARISONS[fact.op] ?? fact.op} ${either(fact.values)}${match}`;
  }
}

function source(explanation: Explanation): string {
  const joins = explanation.joins.map((join) => {
    const on = join.on.map((pair) => pair.join(" = ")).join(" and ");
    const kept = join.kind === "left" ? " (rows without a match kept)" : "";
    return `matched with ${join.table}${on ? ` on ${on}` : ""}${kept}`;
  });
  return [explanation.tables.join(", "), ...joins].filter(Boolean).join(", ");
}

function shown(explanation: Explanation): string | null {
  const { order, limit } = explanation;
  const by = order.map((item) => describeExpr(item.by)).join(", ");
  if (order.length && limit !== null) {
    return `${order[0].descending ? "top" : "bottom"} ${limit} by ${by}`;
  }
  if (order.length) return `sorted by ${by}, ${order[0].descending ? "highest" : "lowest"} first`;
  if (limit !== null) return `the first ${limit} rows`;
  return null;
}

/** One labelled line per part of the query, in the order a person would read them. */
export function explanationLines(explanation: Explanation): { label: string; text: string }[] {
  const lines: { label: string; text: string }[] = [];
  const add = (label: string, text: string | null) => {
    if (text) lines.push({ label, text });
  };
  add("Data", source(explanation));
  add("Calculated", explanation.measures.map(describeExpr).join("; "));
  if (explanation.groups.length === 0) {
    const columns = explanation.shown.map(describeExpr).join(", ");
    add("Shows", columns && explanation.distinct_rows ? `${columns}, each combination once` : columns);
  }
  add("Only rows where", explanation.filters.map(describeFilter).join("; "));
  add("Grouped by", explanation.groups.map(describeExpr).join(", "));
  add("Only groups where", explanation.having.map(describeFilter).join("; "));
  add("Shown", shown(explanation));
  return lines;
}

/** The queries an answer ran, without repeats: a model sometimes runs the same query again to
 * check it, and two copies of one calculation only add noise. Queries count as the same when
 * they read the same in plain words; the first one is kept. */
export function uniqueExplanations(explanations: Explanation[]): Explanation[] {
  const seen = new Set<string>();
  return explanations.filter((explanation) => {
    const key = JSON.stringify([explanation.complete, explanationLines(explanation)]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
