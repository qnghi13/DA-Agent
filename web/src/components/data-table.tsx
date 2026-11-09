import { cn } from "@/lib/utils";

const NUMERIC = /^-?[\d.,]+%?$/;

/** Format one query cell for display. */
export function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

/** A compact, scrollable table of text cells; numbers align right in the mono face. */
export function DataTable({
  columns,
  rows,
  className,
}: {
  columns: string[];
  rows: string[][];
  className?: string;
}) {
  return (
    <div className={cn("overflow-x-auto rounded-lg border", className)}>
      <table className="w-full border-collapse text-sm">
        <thead className="bg-muted">
          <tr>
            {columns.map((column, c) => (
              <th
                key={`${column}-${c}`}
                scope="col"
                className="text-muted-foreground border-b px-3 py-2 text-left text-xs font-medium whitespace-nowrap"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b last:border-0">
              {row.map((value, c) => (
                <td
                  key={c}
                  className={cn(
                    "px-3 py-1.5 align-top",
                    NUMERIC.test(value) && "text-right font-mono text-[13px] tabular-nums",
                  )}
                >
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
