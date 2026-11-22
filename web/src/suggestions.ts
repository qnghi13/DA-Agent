import type { ColumnProfile, ColumnSpec, Dataset, TableSpec } from "./api";
import type { Messages } from "./i18n/en";

/** Questions to start from, built from the dataset's own columns
 * (docs/design/2026-11-15-suggested-questions.md). Code, not a model: every question names a
 * column that exists, and sensitive columns are never named. */

/** Questions for any dataset, when none of its columns fits a better one. */
export function genericQuestions(m: Messages): string[] {
  return [...m.suggestions.generic];
}

const MAX_QUESTIONS = 5;
// A text column with this many different values or fewer reads as a status or a channel.
const MAX_STATUS_VALUES = 10;
// Types say little about what a sum means: a model may type a quantity as an integer and a unit
// price as money. Names fill the gap. A wrong guess here costs only a less useful suggestion.
const QUANTITY_NAME = /\b(so luong|sl|qty|quantity)\b/;
const UNIT_PRICE_NAME = /\b(gia|don gia|price|unit)\b/;
const TOTAL_NAME = /\b(tong|thanh tien|total|amount|doanh thu|revenue)\b/;

/** Lower case without Vietnamese accents, so "Đơn giá" matches "don gia". */
function folded(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").replace(/đ/gi, "d").toLowerCase();
}

/** The column whose sum means something: a quantity first, then money that is not a unit price,
 * then any other number. */
function amountColumn(columns: ColumnSpec[]): ColumnSpec | undefined {
  const name = (c: ColumnSpec) => folded(c.source);
  const quantity = columns.find(
    (c) => c.type === "quantity" || (c.type === "integer" && QUANTITY_NAME.test(name(c))),
  );
  const money = columns.find(
    (c) => c.type === "money" && (TOTAL_NAME.test(name(c)) || !UNIT_PRICE_NAME.test(name(c))),
  );
  return quantity ?? money ?? columns.find((c) => c.type === "number");
}

interface Roles {
  date?: ColumnSpec;
  amount?: ColumnSpec;
  category?: ColumnSpec;
  status?: ColumnSpec;
}

function roles(table: TableSpec, profiles: ColumnProfile[] | undefined): Roles {
  const visible = table.columns.filter((c) => !c.sensitive);
  const date = visible.find((c) => c.type === "date" || c.type === "datetime");
  const amount = amountColumn(visible);
  // How many different values a text column holds is known only from its profile.
  const distinct = (c: ColumnSpec) => profiles?.find((p) => p.name === c.name);
  const repeating = visible.filter((c) => {
    const found = distinct(c);
    return c.type === "text" && found?.distinct != null && found.distinct >= 2 && found.distinct < found.filled;
  });
  const status = repeating.find((c) => distinct(c)!.distinct! <= MAX_STATUS_VALUES);
  const category = repeating.find((c) => distinct(c)!.distinct! > MAX_STATUS_VALUES);
  return { date, amount, category, status };
}

function mainTable(dataset: Dataset): TableSpec | undefined {
  const tables = dataset.spec?.tables ?? [];
  const rows = (t: TableSpec) => dataset.summary?.tables?.find((s) => s.name === t.name)?.rows ?? 0;
  const score = (t: TableSpec) => {
    const found = roles(t, undefined);
    return Number(Boolean(found.date)) + Number(Boolean(found.amount));
  };
  return [...tables].sort((a, b) => score(b) - score(a) || rows(b) - rows(a))[0];
}

/** In the UI language, so clicking one asks in it and the agent answers in it. */
export function suggestQuestions(dataset: Dataset, m: Messages): string[] {
  const words = m.suggestions;
  const table = mainTable(dataset);
  if (!table) return genericQuestions(m);
  const profiles = dataset.summary?.tables?.find((s) => s.name === table.name)?.profile;
  const { date, amount, category, status } = roles(table, profiles);
  const questions: (string | false | undefined)[] = [
    date && amount && words.totalByMonth(amount.source),
    !date && amount && words.total(amount.source),
    category && amount && words.top5(category.source, amount.source),
    status && words.countByStatus(status.source),
    date && amount && words.bestMonth(amount.source),
    date && words.dateRange(date.source),
  ];
  const found = questions.filter((q): q is string => Boolean(q)).slice(0, MAX_QUESTIONS);
  return found.length ? found : genericQuestions(m);
}
