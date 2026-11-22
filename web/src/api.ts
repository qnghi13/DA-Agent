// Typed client for the DA-Agent HTTP API (docs/adr/0020-http-api.md).

import type { Me } from "./auth";
import type { FeedbackReason, Rating, RunFeedback } from "./feedback";
import type { FailureRow, OpsAlert, OpsSummary, Window } from "./ops";
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

/** One visible column of a built table (docs/design/2026-11-14-data-overview.md). */
export interface ColumnProfile {
  name: string;
  type: string;
  filled: number;
  parsed: number;
  failed_samples: string[];
  distinct: number | null;
  min: string | number | null;
  max: string | number | null;
  mean: number | null;
  top: { value: string; rows: number }[];
}

export interface DatasetSummary {
  status?: string;
  /** profile is missing for datasets built before the data overview. */
  tables?: { name: string; rows: number; row: string; columns: number; profile?: ColumnProfile[] }[];
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
  /** The data version runs read; missing for an API from before versions. */
  current_version?: number;
  /** The version being checked or waiting for the user, if any (docs/design/2026-11-21-add-files.md). */
  open_version?: DatasetVersion | null;
}

export type VersionStatus =
  | "uploaded"
  | "checking"
  | "needs_choice"
  | "proposed"
  | "building"
  | "applied"
  | "failed"
  | "discarded";

export type OverlapChoice = "replace" | "drop_overlap" | "keep_both";

export interface VersionOverlap {
  kind: "exact" | "key" | "date";
  rows: number;
  key?: string[];
  column?: string | null;
  examples?: Record<string, string | null>[];
  /** Files of the same upload, earlier in upload order, that already hold some of these rows. */
  earlier_new_files?: string[];
  removed_by_dedupe?: boolean;
}

/** What the check found for one table (src/da_agent/ingestion/version_review.py). */
export interface VersionChange {
  table: string;
  kind: "unchanged" | "grown" | "new" | "removed";
  new_rows: number;
  overlaps: VersionOverlap[];
  near_match: string | null;
  missing_columns: string[];
  extra_columns: string[];
  quality: string[];
  stale_metrics: string[];
  /** Only when the table has overlaps. */
  allowed_choices?: OverlapChoice[];
  note?: string;
}

/** A change to an existing table's spec the model proposed to fix parse problems. */
export interface RepairChange {
  table: string;
  path: string;
  before: unknown;
  after: unknown;
}

export interface DatasetVersion {
  version: number;
  status: VersionStatus;
  files: { name: string; stored_as: string; bytes: number }[];
  spec: DatasetSpec | null;
  summary: (DatasetSummary & { repair_problem?: string }) | null;
  changes: VersionChange[] | null;
  choices: Record<string, unknown> | null;
  repairs: RepairChange[] | null;
  error: string | null;
  created_at: string;
}

export interface VersionApproval {
  choices: Record<string, OverlapChoice>;
  replace_files: Record<string, string>;
  /** Edited specs of new tables only, or null to keep the proposal. */
  spec: DatasetSpec | null;
  repairs: RepairChange[];
  apply_anyway: boolean;
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

  /** Upload more files as the dataset's next version; `replaces` has one stored name or null per file. */
  addFiles(
    datasetId: string,
    files: File[],
    replaces: (string | null)[],
  ): Promise<{ dataset_id: string; version: number; status: VersionStatus }> {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    for (const replaced of replaces) form.append("replaces", replaced ?? "");
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/files`, {
      method: "POST",
      body: form,
    });
  }

  listVersions(datasetId: string): Promise<{ versions: DatasetVersion[] }> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/versions`);
  }

  approveVersion(
    datasetId: string,
    n: number,
    body: VersionApproval,
  ): Promise<{ dataset_id: string; version: number; status: VersionStatus }> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/versions/${n}/approve`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
  }

  discardVersion(datasetId: string, n: number): Promise<void> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/versions/${n}/discard`, {
      method: "POST",
    });
  }

  /** A run reads the earlier turns of the conversation it names (docs/adr/0030). */
  createRun(
    datasetId: string,
    question: string,
    asOf?: string,
    conversationId?: string | null,
  ): Promise<{ id: string }> {
    return this.request("/v1/runs", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        dataset_id: datasetId,
        question,
        ...(asOf ? { as_of: asOf } : {}),
        ...(conversationId ? { conversation_id: conversationId } : {}),
      }),
    });
  }

  createConversation(datasetId: string): Promise<{ id: string }> {
    return this.request("/v1/conversations", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ dataset_id: datasetId }),
    });
  }

  /** Every note of a dataset: suggested, approved, rejected, and replaced. */
  listMemory(datasetId: string): Promise<{ memory: MemoryEntry[] }> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/memory`);
  }

  /** A note the caller writes is approved at once; later runs read it. */
  writeMemory(datasetId: string, note: NewMemory): Promise<{ id: string }> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/memory`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(note),
    });
  }

  /** Replaces the note with a new approved one; returns the new id. */
  editMemory(id: string, text: string, sql?: string | null): Promise<{ id: string }> {
    return this.request(`/v1/memory/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ text, ...(sql ? { sql } : {}) }),
    });
  }

  /** Saves one measure of a run's answer as a definition; the server reads it from the run. */
  saveMetric(
    datasetId: string,
    body: { run_id: string; query_index: number; candidate_index: number; name: string; aliases: string[] },
  ): Promise<{ id: string }> {
    return this.request(`/v1/datasets/${encodeURIComponent(datasetId)}/metrics`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
  }

  /** Renames a definition; its calculation stays. Returns the new id. */
  renameMetric(id: string, name: string, aliases: string[]): Promise<{ id: string }> {
    return this.request(`/v1/memory/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ name, aliases }),
    });
  }

  reviewMemory(id: string, decision: "approve" | "reject"): Promise<unknown> {
    return this.request(`/v1/memory/${encodeURIComponent(id)}/${decision}`, { method: "POST" });
  }

  deleteMemory(id: string): Promise<void> {
    return this.request(`/v1/memory/${encodeURIComponent(id)}`, { method: "DELETE" });
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

  /** Rate a finished run; a thumbs-up on an answer makes it a golden case (ADR 0043). */
  setFeedback(
    id: string,
    feedback: { rating: Rating; reasons?: FeedbackReason[]; note?: string },
  ): Promise<RunFeedback> {
    return this.request(`/v1/runs/${encodeURIComponent(id)}/feedback`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(feedback),
    });
  }

  clearFeedback(id: string): Promise<void> {
    return this.request(`/v1/runs/${encodeURIComponent(id)}/feedback`, { method: "DELETE" });
  }

  /** Stop a run. A queued run ends at once; a running one stops within a few seconds. */
  cancelRun(id: string): Promise<{ id: string; status: "cancelled" | "cancelling" }> {
    return this.request(`/v1/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
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

  /** Alerts firing now, read from Alertmanager (docs/adr/0044). */
  getOpsAlerts(): Promise<{ alerts: OpsAlert[]; available: boolean }> {
    return this.request("/v1/ops/alerts");
  }

  /** Jobs that failed, wait to retry, or stopped without an answer; newest first. */
  getOpsFailures(before?: string): Promise<{ failures: FailureRow[] }> {
    const params = new URLSearchParams({ limit: "25" });
    if (before) params.set("before", before);
    return this.request(`/v1/ops/failures?${params}`);
  }

  /** Start writing the whole result of one query of a run as CSV. `index` counts from 0. */
  startExport(runId: string, index: number): Promise<{ id: string; status: string }> {
    return this.request(`/v1/runs/${encodeURIComponent(runId)}/queries/${index}/export`, {
      method: "POST",
    });
  }

  getExport(id: string): Promise<ExportJob> {
    return this.request(`/v1/exports/${encodeURIComponent(id)}`);
  }

  /** Run SQL edited from one of a run's queries. Bad SQL is refused at once with a 422. */
  runQuery(runId: string, sql: string): Promise<{ id: string; status: string }> {
    return this.request(`/v1/runs/${encodeURIComponent(runId)}/queries`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
  }

  getQuery(id: string): Promise<QueryJob> {
    return this.request(`/v1/queries/${encodeURIComponent(id)}`);
  }

  startQueryExport(queryId: string): Promise<{ id: string; status: string }> {
    return this.request(`/v1/queries/${encodeURIComponent(queryId)}/export`, { method: "POST" });
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

/** An export job (docs/design/2026-11-14-result-export.md). `error` is set when it failed. */
export interface ExportJob {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  rows: number | null;
  bytes: number | null;
  filename: string | null;
  error: string | null;
}

/** Where a finished export downloads from; the session cookie goes with it. */
/** An edited query run as a job (docs/design/2026-11-14-edit-sql.md). */
export interface QueryJob {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  sql: string;
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  error: string | null;
}

export function exportFileUrl(id: string): string {
  return `/v1/exports/${encodeURIComponent(id)}/file`;
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

export interface MemoryEntry {
  id: string;
  kind: "correction" | "alias" | "example" | "metric";
  text: string;
  sql: string | null;
  status: "proposed" | "approved" | "rejected";
  author: string;
  source_run_id: string | null;
  superseded_by: string | null;
  /** Set for metric definitions only. */
  name?: string;
  aliases?: string[];
  facts?: MetricFacts;
  /** The dataset no longer has a column the definition reads; runs leave it out. */
  stale?: boolean;
}

/** What a metric definition computes, as facts read from the SQL it was saved from. */
export interface MetricFacts {
  measure: ExprFact;
  filters: FilterFact[];
}

export interface NewMemory {
  kind: Exclude<MemoryEntry["kind"], "metric">;
  text: string;
  sql?: string;
  source_run_id?: string;
}

/** A run's job status. "cancelled" means the user stopped it (ADR 0042). */
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Run {
  id: string;
  dataset_id: string;
  question: string;
  as_of: string;
  conversation_id: string | null;
  /** The data version it read; missing for runs from before versions, which read version 1. */
  dataset_version?: number;
  status: RunStatus;
  /** The user asked to stop it; a running run keeps this until its worker stops. */
  cancel_requested?: boolean;
  attempts: number;
  error: string | null;
  result: RunResult | null;
  /** One link per attempt, present only when an operator configured a trace viewer. */
  traces: { attempt: number; url: string }[];
  /** How the answer was calculated, read from its SQL (ADR 0035). Only in a single run's view. */
  explanations?: Explanation[];
  /** The member's rating (ADR 0043). Only in a single run's view. */
  feedback?: RunFeedback | null;
}

/** A value in a query: a column, an aggregate, arithmetic, a number, or every column. */
export interface ExprFact {
  kind: "column" | "aggregate" | "arithmetic" | "number" | "all";
  name?: string | null;
  column?: string;
  unit?: string | null;
  func?: string;
  of?: ExprFact | null;
  distinct?: boolean;
  condition?: FilterFact[];
  op?: string;
  left?: ExprFact;
  right?: ExprFact;
  value?: number;
}

export interface FilterFact {
  of: ExprFact;
  op: string;
  /** Literals, or an ExprFact when two columns are compared. */
  values: unknown[];
  match: string;
}

export interface Explanation {
  query_index: number;
  tables: string[];
  joins: { table: string; kind: string; on: string[][] }[];
  measures: ExprFact[];
  shown: ExprFact[];
  filters: FilterFact[];
  groups: ExprFact[];
  having: FilterFact[];
  order: { by: ExprFact; descending: boolean }[];
  limit: number | null;
  distinct_rows: boolean;
  /** Parts not put in words, such as "subquery"; the SQL tab shows them. */
  unexplained: string[];
  complete: boolean;
  /** Measures of this query that can be saved as a metric definition. */
  metric_candidates?: { index: number; facts: MetricFacts }[];
  /** Saved definitions this query used, found by reading its SQL. */
  metrics_used?: { id: string; name: string }[];
}

export interface RunSummary {
  id: string;
  question: string;
  as_of: string | null;
  status: Run["status"];
  created_at: string;
  conversation_id: string | null;
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
