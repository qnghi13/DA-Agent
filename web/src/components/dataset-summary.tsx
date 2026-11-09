import { ChevronRight, FileText } from "lucide-react";
import type { Dataset } from "@/api";
import { cn } from "@/lib/utils";

/** Files, tables, and the build's notes for one dataset; the approved spec when it has one. */
export function DatasetSummary({ dataset, showSpec = true }: { dataset: Dataset; showSpec?: boolean }) {
  const summary = dataset.summary;
  return (
    <div className="flex flex-col gap-6">
      {summary?.tables && summary.tables.length > 0 && (
        <Section title="Tables">
          <div className="grid gap-2">
            {summary.tables.map((table) => (
              <div key={table.name} className="rounded-lg border px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-3">
                  <code className="text-sm font-medium">{table.name}</code>
                  <span className="text-muted-foreground font-mono text-xs tabular-nums">
                    {table.rows.toLocaleString()} rows · {table.columns} cols
                  </span>
                </div>
                {table.row && (
                  <p className="text-muted-foreground mt-1 text-sm">{table.row}</p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      <Notes title="Problems to check" tone="warning" items={summary?.problems} />
      <Notes title="Proposal problems" tone="warning" items={summary?.proposal_problems} />
      <Notes title="Exclusion rules dropped" tone="info" items={summary?.dropped_rules} />
      <Notes title="Exact repeats removed" tone="info" items={summary?.deduplicated} />

      <Section title={`Files (${dataset.files.length})`}>
        <ul className="divide-y rounded-lg border">
          {dataset.files.map((file) => (
            <li key={file.stored_as} className="flex items-center gap-2 px-3 py-2 text-sm">
              <FileText className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="min-w-0 truncate">{file.name}</span>
              {file.stored_as !== file.name && (
                <span className="text-muted-foreground truncate text-xs">
                  stored as {file.stored_as}
                </span>
              )}
            </li>
          ))}
        </ul>
        {dataset.documents > 0 && (
          <p className="text-muted-foreground mt-2 text-xs">
            {dataset.documents === 1 ? "1 document explains" : `${dataset.documents} documents explain`}{" "}
            the data.
          </p>
        )}
      </Section>

      {dataset.description && (
        <Section title="Description">
          <p className="text-muted-foreground text-sm leading-relaxed whitespace-pre-wrap">
            {dataset.description}
          </p>
        </Section>
      )}

      {showSpec && dataset.spec && (
        <details className="group rounded-lg border">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium">
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" />
            Approved spec
          </summary>
          <pre className="max-h-96 overflow-auto border-t p-3 font-mono text-xs leading-relaxed">
            {JSON.stringify(dataset.spec, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
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
