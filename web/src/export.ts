import type { ExportJob } from "./api";

/** Poll an export job until it has finished, either way (docs/design/2026-11-14-result-export.md). */
export async function waitForExport(
  get: (id: string) => Promise<ExportJob>,
  id: string,
  {
    intervalMs = 1000,
    timeoutMs = 180_000,
    sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms)),
  }: { intervalMs?: number; timeoutMs?: number; sleep?: (ms: number) => Promise<void> } = {},
): Promise<ExportJob> {
  for (let waited = 0; ; waited += intervalMs) {
    const job = await get(id);
    if (job.status === "succeeded" || job.status === "failed") return job;
    if (waited + intervalMs > timeoutMs) throw new Error("the export is taking too long");
    await sleep(intervalMs);
  }
}

/** A file name from a chart title: ASCII letters and digits joined by dashes, or "chart". */
export function chartFilename(title: string | undefined, extension: "png" | "svg"): string {
  const slug = (title ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${slug || "chart"}.${extension}`;
}
