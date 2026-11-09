import { describe, expect, it } from "vitest";
import { DATASET_STATUSES, datasetStatus, setupStep } from "./status";

describe("datasetStatus", () => {
  it("labels every status with a tone that means the same thing everywhere", () => {
    expect(datasetStatus("ready")).toEqual({ label: "Ready", tone: "success", busy: false });
    expect(datasetStatus("proposed")).toEqual({ label: "Review", tone: "warning", busy: false });
    expect(datasetStatus("uploaded")).toEqual({ label: "Reading", tone: "info", busy: true });
    expect(datasetStatus("building")).toEqual({ label: "Building", tone: "info", busy: true });
    expect(datasetStatus("failed")).toEqual({ label: "Failed", tone: "danger", busy: false });
  });

  it("covers every status the API defines", () => {
    for (const status of DATASET_STATUSES) expect(datasetStatus(status).label).toBeTruthy();
  });
});

describe("setupStep", () => {
  it("places a dataset on the Upload, Review, Ready path", () => {
    expect(setupStep(null)).toBe(1);
    expect(setupStep("uploaded")).toBe(2);
    expect(setupStep("proposed")).toBe(2);
    expect(setupStep("building")).toBe(2);
    expect(setupStep("failed")).toBe(2);
    expect(setupStep("ready")).toBe(3);
  });
});
