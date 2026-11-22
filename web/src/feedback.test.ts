import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { feedbackReasons, nextFeedback } from "./feedback";
import { en } from "./i18n/en";
import { vi as viMessages } from "./i18n/vi";

describe("feedback on an answer", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rates, switches, and takes back a rating", () => {
    expect(nextFeedback(null, "up")).toBe("up");
    expect(nextFeedback("up", "down")).toBe("down");
    expect(nextFeedback("down", "down")).toBeNull();
  });

  it("offers the reasons the API accepts, in plain words", () => {
    expect(feedbackReasons(en).map((reason) => reason.value)).toEqual([
      "wrong_number",
      "misunderstood",
      "should_abstain",
      "unclear",
      "other",
    ]);
    expect(feedbackReasons(en).every((reason) => reason.label.length > 0)).toBe(true);
    const vietnamese = feedbackReasons(viMessages);
    expect(vietnamese.map((reason) => reason.value)).toEqual(feedbackReasons(en).map((r) => r.value));
    expect(vietnamese[0].label).not.toBe(feedbackReasons(en)[0].label);
  });

  it("puts and deletes the run's feedback", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "DELETE"
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ rating: "down", reasons: ["unclear"], note: null })),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = new Api();

    const saved = await api.setFeedback("r/1", { rating: "down", reasons: ["unclear"] });
    await api.clearFeedback("r/1");

    expect(saved.reasons).toEqual(["unclear"]);
    const [putUrl, putInit] = fetchMock.mock.calls[0];
    expect(putUrl).toBe("/v1/runs/r%2F1/feedback");
    expect(putInit?.method).toBe("PUT");
    expect(JSON.parse(String(putInit?.body))).toEqual({ rating: "down", reasons: ["unclear"] });
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });
});
