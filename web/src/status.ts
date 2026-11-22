import { BUSY_STATUSES, type DatasetStatus } from "./api";
import type { Messages } from "./i18n/en";

/** Colour carries status only, with one meaning across the UI. */
export type Tone = "success" | "info" | "warning" | "danger" | "neutral";

export const DATASET_STATUSES: readonly DatasetStatus[] = [
  "uploaded",
  "proposed",
  "building",
  "ready",
  "failed",
];

const TONES: Record<DatasetStatus, Tone> = {
  uploaded: "info",
  proposed: "warning",
  building: "info",
  ready: "success",
  failed: "danger",
};

export function datasetStatus(
  status: DatasetStatus,
  m: Messages,
): { label: string; tone: Tone; busy: boolean } {
  return { label: m.status[status], tone: TONES[status], busy: BUSY_STATUSES.includes(status) };
}

/** Where a dataset sits on Upload (1) → Review spec (2) → Ready (3); null is a new upload. */
export function setupStep(status: DatasetStatus | null): 1 | 2 | 3 {
  if (status === null) return 1;
  return status === "ready" ? 3 : 2;
}
