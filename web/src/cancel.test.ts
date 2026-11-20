import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
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
    expect(answerSummary("cancelled", null)).toEqual({
      label: "Stopped",
      tone: "neutral",
      detail: null,
    });
    expect(stopReasonText("cancelled")).toBe("You stopped this question.");
    expect(stopReasonText("deadline_exceeded")).toBe("Stopped: this question ran out of time.");
  });

  it("treats only queued and running runs as active", () => {
    expect(isRunActive("queued")).toBe(true);
    expect(isRunActive("running")).toBe(true);
    for (const status of ["succeeded", "failed", "cancelled"] as const) {
      expect(isRunActive(status)).toBe(false);
    }
  });
});
