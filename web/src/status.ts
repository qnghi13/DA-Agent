import { BUSY_STATUSES, type DatasetStatus } from "./api";

/** Colour carries status only, with one meaning across the UI. */
export type Tone = "success" | "info" | "warning" | "danger" | "neutral";

export const DATASET_STATUSES: readonly DatasetStatus[] = [
  "uploaded",
  "proposed",
  "building",
  "ready",
  "failed",
];

const LABELS: Record<DatasetStatus, { label: string; tone: Tone }> = {
  uploaded: { label: "Reading", tone: "info" },
  proposed: { label: "Review", tone: "warning" },
  building: { label: "Building", tone: "info" },
  ready: { label: "Ready", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function datasetStatus(status: DatasetStatus): { label: string; tone: Tone; busy: boolean } {
  return { ...LABELS[status], busy: BUSY_STATUSES.includes(status) };
}

/** Where a dataset sits on Upload (1) → Review spec (2) → Ready (3); null is a new upload. */
export function setupStep(status: DatasetStatus | null): 1 | 2 | 3 {
  if (status === null) return 1;
  return status === "ready" ? 3 : 2;
}
