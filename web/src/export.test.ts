import { describe, expect, it } from "vitest";
import type { ExportJob } from "./api";
import { chartFilename, waitForExport } from "./export";

const job = (status: ExportJob["status"], extra: Partial<ExportJob> = {}): ExportJob => ({
  id: "e1",
  status,
  rows: null,
  bytes: null,
  filename: null,
  error: null,
  ...extra,
});

function sequence(jobs: ExportJob[]) {
  const asked: string[] = [];
  return {
    asked,
    get: (id: string) => {
      asked.push(id);
      return Promise.resolve(jobs[Math.min(asked.length - 1, jobs.length - 1)]);
    },
  };
}

const noSleep = () => Promise.resolve();

describe("waitForExport", () => {
  it("resolves when the export succeeds", async () => {
    const done = job("succeeded", { rows: 2, filename: "a.csv" });
    const { get, asked } = sequence([job("queued"), job("running"), done]);

    await expect(waitForExport(get, "e1", { sleep: noSleep })).resolves.toEqual(done);
    expect(asked).toEqual(["e1", "e1", "e1"]);
  });

  it("resolves with the error when it fails", async () => {
    const failed = job("failed", { error: "the result has more than 1,000,000 rows" });
    const { get } = sequence([job("queued"), failed]);

    await expect(waitForExport(get, "e1", { sleep: noSleep })).resolves.toEqual(failed);
  });

  it("keeps waiting while the export is queued behind other jobs", async () => {
    const done = job("succeeded", { rows: 2, filename: "a.csv" });
    const { get, asked } = sequence([...Array(400).fill(job("queued")), done]);

    await expect(waitForExport(get, "e1", { sleep: noSleep, intervalMs: 1000 })).resolves.toEqual(
      done,
    );
    expect(asked).toHaveLength(401);
  });

  it("stops polling when aborted", async () => {
    const controller = new AbortController();
    const { get, asked } = sequence([job("running")]);
    const sleep = () => {
      controller.abort();
      return Promise.resolve();
    };

    await expect(waitForExport(get, "e1", { sleep, signal: controller.signal })).rejects.toThrow(
      "aborted",
    );
    expect(asked).toEqual(["e1"]);
  });
});

describe("chartFilename", () => {
  it("slugs the title, keeping Vietnamese letters readable", () => {
    expect(chartFilename("Doanh thu tháng 6/2026", "png")).toBe("doanh-thu-thang-6-2026.png");
  });

  it("falls back to chart", () => {
    expect(chartFilename(undefined, "svg")).toBe("chart.svg");
    expect(chartFilename("!!!", "svg")).toBe("chart.svg");
  });
});
