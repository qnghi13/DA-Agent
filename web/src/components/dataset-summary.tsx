import { ChevronRight, FileText } from "lucide-react";
import type { ColumnProfile, Dataset } from "@/api";
import { columnDetail, share } from "@/profile";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/context";
import type { Messages } from "@/i18n/en";

/** Files, tables, and the build's notes for one dataset; the approved spec when it has one. */
export function DatasetSummary({ dataset, showSpec = true }: { dataset: Dataset; showSpec?: boolean }) {
  const m = useT();
  const words = m.summary;
  const number = new Intl.NumberFormat(m.locale);
  const summary = dataset.summary;
  return (
    <div className="flex flex-col gap-6">
      {summary?.tables && summary.tables.length > 0 && (
        <Section title={words.tables}>
          <div className="grid gap-2">
            {summary.tables.map((table) => (
              <div key={table.name} className="rounded-lg border px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-3">
                  <code className="text-sm font-medium">{table.name}</code>
                  <span className="text-muted-foreground font-mono text-xs tabular-nums">
                    {words.size(number.format(table.rows), table.columns)}
                  </span>
                </div>
                {table.row && (
                  <p className="text-muted-foreground mt-1 text-sm">{table.row}</p>
                )}
                {table.profile && table.profile.length > 0 && (
                  <Columns columns={table.profile} rows={table.rows} />
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      <Notes title={words.problems} tone="warning" items={summary?.problems} />
      <Notes title={words.proposalProblems} tone="warning" items={summary?.proposal_problems} />
      <Notes title={words.droppedRules} tone="info" items={summary?.dropped_rules} />
      <Notes title={words.deduplicated} tone="info" items={summary?.deduplicated} />

      <Section title={words.files(dataset.files.length)}>
        <ul className="divide-y rounded-lg border">
          {dataset.files.map((file) => (
            <li key={file.stored_as} className="flex items-center gap-2 px-3 py-2 text-sm">
              <FileText className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="min-w-0 truncate">{file.name}</span>
              {file.stored_as !== file.name && (
                <span className="text-muted-foreground truncate text-xs">
                  {words.storedAs(file.stored_as)}
                </span>
              )}
            </li>
          ))}
        </ul>
        {dataset.documents > 0 && (
          <p className="text-muted-foreground mt-2 text-xs">
            {words.documents(dataset.documents)}
          </p>
        )}
      </Section>

      {dataset.description && (
        <Section title={m.spec.description}>
          <p className="text-muted-foreground text-sm leading-relaxed whitespace-pre-wrap">
            {dataset.description}
          </p>
        </Section>
      )}

      {showSpec && dataset.spec && (
        <details className="group rounded-lg border">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium">
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" />
            {words.approvedSpec}
          </summary>
          <pre className="max-h-96 overflow-auto border-t p-3 font-mono text-xs leading-relaxed">
            {JSON.stringify(dataset.spec, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

/** The data overview of one table: each column's type, how full it is, and what it holds. */
function Columns({ columns, rows }: { columns: ColumnProfile[]; rows: number }) {
  const m = useT();
  return (
    <details className="group mt-2">
      <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1 text-xs">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
        {m.spec.columns}
      </summary>
      <ul className="mt-2 divide-y border-t">
        {columns.map((column) => {
          const failed = column.filled - column.parsed;
          const detail = columnDetail(column, m);
          return (
            <li key={column.name} className="flex flex-col gap-0.5 py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate">
                  <code className="text-sm">{column.name}</code>{" "}
                  <span className="text-muted-foreground text-xs">{typeName(column.type, m)}</span>
                </span>
                <span className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums">
                  {m.summary.filled(share(column.filled, rows, m))}
                  {column.distinct !== null &&
                    m.summary.distinct(new Intl.NumberFormat(m.locale).format(column.distinct))}
                </span>
              </div>
              {detail && (
                <p className="text-muted-foreground truncate text-xs" title={detail}>
                  {detail}
                </p>
              )}
              {failed > 0 && (
                <p className="text-warning text-xs">
                  {m.summary.notRead(
                    new Intl.NumberFormat(m.locale).format(failed),
                    failed,
                    typeName(column.type, m),
                    column.failed_samples.map((v) => `“${v}”`).join(", "),
                  )}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function typeName(type: string, m: Messages): string {
  return (m.spec.types as Record<string, string>)[type] ?? type;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-muted-foreground mb-2 text-xs font-medium">{title}</h3>
      {children}
    </section>
  );
}

function Notes({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "warning" | "info";
  items: string[] | undefined;
}) {
  if (!items?.length) return null;
  return (
    <section
      className={cn(
        "rounded-lg border px-3 py-2.5",
        tone === "warning" ? "border-warning-line bg-warning-soft" : "border-info-line bg-info-soft",
      )}
    >
      <h3 className={cn("mb-1 text-sm font-medium", tone === "warning" ? "text-warning" : "text-info")}>
        {title}
      </h3>
      <ul className="list-disc pl-5 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
