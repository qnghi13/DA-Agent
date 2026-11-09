// Typed client for the DA-Agent HTTP API (docs/adr/0020-http-api.md).

import type { Me } from "./auth";
import type { FailureRow, OpsSummary, Window } from "./ops";
import type { Answer } from "./run";
import { type SseEvent, streamEvents } from "./sse";

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
/** The API's defaults (ApiSettings.max_files and max_file_bytes); it enforces them. */
export const UPLOAD_LIMITS = { files: 50, bytesPerFile: 200 * 1024 * 1024 };

async function send<T>(path: string, init: RequestInit = {}): Promise<T> {
  // The session cookie goes with every same-origin request; the browser adds Origin to POSTs,
  // which the API checks (docs/adr/0025).
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Who this browser is signed in as. Rejects with a 401 ApiError when it is not. */
export function fetchMe(): Promise<Me> {
  return send("/v1/auth/me");
}

/** Use a one-time login link. */
export function redeemLink(token: string): Promise<Me> {
  return send("/v1/auth/redeem", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ token }),
  });
}

/** Exchange an API key for a session once; the browser does not keep the key. */
export function signInWithKey(key: string): Promise<Me> {
  return send("/v1/auth/session", { method: "POST", headers: { Authorization: `Bearer ${key}` } });
}

export function signOut(): Promise<void> {
  return send("/v1/auth/logout", { method: "POST" });
}

/** A one-time link that signs another browser in with the caller's role. */
export function createLoginLink(): Promise<{ url: string; expires_in_seconds: number }> {
  return send("/v1/auth/links", { method: "POST", headers: JSON_HEADERS, body: "{}" });
}

export class Api {
  /** `onSignedOut` runs when the session has ended, such as after expiry or revocation. */
  constructor(private readonly onSignedOut: () => void = () => {}) {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    try {
      return await send<T>(path, init);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) this.onSignedOut();
      throw caught;
    }
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

  createRun(datasetId: string, question: string, asOf?: string): Promise<{ id: string }> {
    return this.request("/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: datasetId, question, ...(asOf ? { as_of: asOf } : {}) }),
    });
  }

  /** A dataset's runs, newest first. Pass next_before back as `before` for older ones. */
  listRuns(
    datasetId: string,
    before?: string,
  ): Promise<{ runs: RunSummary[]; next_before: string | null }> {
    const params = new URLSearchParams({ dataset_id: datasetId, limit: "20" });
    if (before) params.set("before", before);
    return this.request(`/v1/runs?${params}`);
  }

  getRun(id: string): Promise<Run> {
    return this.request(`/v1/runs/${encodeURIComponent(id)}`);
  }

  /** The run's events; reconnects until the run ends. */
  streamRunEvents(id: string, onEvent: (event: SseEvent) => void, signal: AbortSignal) {
    return streamEvents(`/v1/runs/${encodeURIComponent(id)}/events`, {
      onEvent,
      signal,
    });
  }

  /** Operator dashboard panels for a window (docs/adr/0026). */
  getOpsSummary(window: Window): Promise<OpsSummary> {
    return this.request(`/v1/ops/summary?window=${window}`);
  }

  /** Jobs that failed, wait to retry, or stopped without an answer; newest first. */
  getOpsFailures(before?: string): Promise<{ failures: FailureRow[] }> {
    const params = new URLSearchParams({ limit: "25" });
    if (before) params.set("before", before);
    return this.request(`/v1/ops/failures?${params}`);
  }

  /** The standalone HTML report, shown in a sandboxed frame. */
  async getReport(id: string): Promise<string> {
    const response = await fetch(`/v1/runs/${encodeURIComponent(id)}/report`, {
      credentials: "same-origin",
    });
    if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
    return response.text();
  }
}

export interface QueryRecord {
  sql: string;
  columns: string[];
  rows: unknown[][];
  truncated?: boolean;
}

export interface RunResult {
  answer: Answer | null;
  stop_reason: string;
  steps: { tool: string; ok: boolean; sql?: string | null; chart?: VegaLiteSpec | null }[];
  iterations: number;
  error: string | null;
  question_for_user: string | null;
  queries: QueryRecord[];
}

export type VegaLiteSpec = Record<string, unknown>;

export interface Run {
  id: string;
  dataset_id: string;
  question: string;
  as_of: string;
  status: "queued" | "running" | "succeeded" | "failed";
  attempts: number;
  error: string | null;
  result: RunResult | null;
  /** One link per attempt, present only when an operator configured a trace viewer. */
  traces: { attempt: number; url: string }[];
}

export interface RunSummary {
  id: string;
  question: string;
  as_of: string | null;
  status: Run["status"];
  created_at: string;
}

export function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 401) return "You are signed out.";
  if (caught instanceof ApiError && caught.status === 429) return `Slow down: ${caught.message}`;
  return caught instanceof Error ? caught.message : String(caught);
}

export async function errorDetail(response: Response): Promise<string> {
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
