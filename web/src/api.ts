// Typed client for the DA-Agent HTTP API (docs/adr/0020-http-api.md).

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// Mirrors src/da_agent/ingestion/spec.py. Unknown fields are kept so edits round-trip.
export const COLUMN_TYPES = [
  "text",
  "integer",
  "number",
  "money",
  "quantity",
  "date",
  "datetime",
  "boolean",
] as const;
export type ColumnType = (typeof COLUMN_TYPES)[number];

export interface ColumnSpec {
  source: string;
  name: string;
  type: ColumnType;
  date_formats?: string[];
  true_values?: string[];
  sensitive?: boolean;
  description?: string;
}

export interface RowRule {
  when: { column: string; op: string; value?: string }[];
  reason?: string;
}

export interface TableSpec {
  source: string;
  name: string;
  description?: string;
  columns: ColumnSpec[];
  fill_down?: string[];
  dedupe?: "none" | "exact";
  exclude_rows?: RowRule[];
}

export interface DatasetSpec {
  tables: TableSpec[];
  relationships?: { from_table: string; from_column: string; to_table: string; to_column: string }[];
  notes?: string[];
}

export interface DatasetSummary {
  status?: string;
  tables?: { name: string; rows: number; row: string; columns: number }[];
  problems?: string[];
  proposal_problems?: string[];
  dropped_rules?: string[];
  deduplicated?: string[];
}

export type DatasetStatus = "uploaded" | "proposed" | "building" | "ready" | "failed";

export interface Dataset {
  id: string;
  name: string;
  description: string;
  status: DatasetStatus;
  spec: DatasetSpec | null;
  summary: DatasetSummary | null;
  error: string | null;
  files: { name: string; stored_as: string; bytes: number }[];
  documents: number;
}

// Statuses a worker is still changing; the UI polls while a dataset has one.
export const BUSY_STATUSES: readonly DatasetStatus[] = ["uploaded", "building"];

// Extensions the API accepts (src/da_agent/ingestion/raw.py).
export const UPLOAD_ACCEPT = ".csv,.tsv,.xlsx,.xlsm,.sqlite,.sqlite3,.db,.md,.txt";

export class Api {
  constructor(private readonly key: string) {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.key}`);
    const response = await fetch(path, { ...init, headers });
    if (!response.ok) {
      throw new ApiError(response.status, await errorDetail(response));
    }
    return (await response.json()) as T;
  }

  listDatasets(): Promise<{ datasets: Dataset[] }> {
    return this.request("/v1/datasets");
  }

  getDataset(id: string): Promise<Dataset> {
    return this.request(`/v1/datasets/${encodeURIComponent(id)}`);
  }

  uploadDataset(name: string, description: string, files: File[]): Promise<{ id: string }> {
    const form = new FormData();
    form.set("name", name);
    form.set("description", description);
    for (const file of files) form.append("files", file, file.name);
    return this.request("/v1/datasets", { method: "POST", body: form });
  }

  /** Approve the proposed spec, or send an edited one to be rebuilt. */
  approveDataset(id: string, spec?: DatasetSpec): Promise<{ id: string; status: DatasetStatus }> {
    return this.request(`/v1/datasets/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec ? { spec } : {}),
    });
  }
}

export function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 401) return "The API key was rejected.";
  if (caught instanceof ApiError && caught.status === 429) return `Slow down: ${caught.message}`;
  return caught instanceof Error ? caught.message : String(caught);
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    // FastAPI validation errors: a list of {loc, msg}.
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item: { loc?: unknown[]; msg?: string }) =>
          [item.loc?.join("."), item.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    }
  } catch {
    // Not JSON: fall back to the status text.
  }
  return response.statusText || `HTTP ${response.status}`;
}
