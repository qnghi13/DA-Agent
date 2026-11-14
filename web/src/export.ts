/**
 * Poll a job (an export or an edited query) until it has finished, either way
 * (docs/design/2026-11-14-result-export.md). There is no deadline here: the job may wait behind
 * other jobs, and the server ends every job (sandbox timeout, job leases). The caller aborts when
 * the view goes away.
 */
export async function waitForJob<T extends { status: string }>(
  get: (id: string) => Promise<T>,
  id: string,
  {
    intervalMs = 1000,
    signal,
    sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms)),
  }: { intervalMs?: number; signal?: AbortSignal; sleep?: (ms: number) => Promise<void> } = {},
): Promise<T> {
  for (;;) {
    signal?.throwIfAborted();
    const job = await get(id);
    if (job.status === "succeeded" || job.status === "failed") return job;
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
