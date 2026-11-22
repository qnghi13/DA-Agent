import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { en } from "./i18n/en";
import { answerSummary, initialTrail, isRunActive, reduceTrail, stopReasonText } from "./run";

const end = (status: string) => ({ id: undefined, event: "end", data: JSON.stringify({ status }) });

describe("cancelling a run", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts to the run's cancel path", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ id: "r/1", status: "cancelling" }), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const reply = await new Api().cancelRun("r/1");

    expect(reply.status).toBe("cancelling");
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/runs/r%2F1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("ends the trail as cancelled, not failed", () => {
    expect(reduceTrail(initialTrail(), end("cancelled")).status).toBe("cancelled");
    expect(reduceTrail(initialTrail(), end("failed")).status).toBe("failed");
  });

  it("labels a cancelled run Stopped, without calling it a failure", () => {
    expect(answerSummary("cancelled", null, en)).toEqual({
      label: "Stopped",
      tone: "neutral",
      detail: null,
    });
    expect(stopReasonText("cancelled", en)).toBe("You stopped this question.");
    expect(stopReasonText("deadline_exceeded", en)).toBe("Stopped: this question ran out of time.");
  });

  it("treats only queued and running runs as active", () => {
    expect(isRunActive("queued")).toBe(true);
    expect(isRunActive("running")).toBe(true);
    for (const status of ["succeeded", "failed", "cancelled"] as const) {
      expect(isRunActive(status)).toBe(false);
    }
  });
});

describe("the composer while a run is active", () => {
  it("offers Stop only for the latest run of the conversation being written in", async () => {
    const { composerStops } = await import("./run");
    const latest = { id: "r2", conversation_id: "c1" };

    expect(composerStops(latest, "c1", { r2: "running" })).toBe(true);
    expect(composerStops(latest, "c1", { r2: "succeeded" })).toBe(false);
    expect(composerStops(latest, "c2", { r2: "running" })).toBe(false);
    expect(composerStops(latest, null, { r2: "running" })).toBe(false);
    expect(composerStops(undefined, "c1", {})).toBe(false);
  });

  it("offers query actions only on runs that finished", async () => {
    const { canActOnQueries } = await import("./run");

    expect(canActOnQueries("succeeded")).toBe(true);
    for (const status of ["queued", "running", "failed", "cancelled"] as const) {
      expect(canActOnQueries(status)).toBe(false);
    }
  });
});
