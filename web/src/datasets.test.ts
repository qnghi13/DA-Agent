import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { en } from "./i18n/en";
import { vi as viMessages } from "./i18n/vi";

describe("deleting a dataset", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("deletes a dataset by id", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify({ id: "d/1", status: "deleting" }), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await new Api().deleteDataset("d/1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/v1/datasets/d%2F1");
    expect(init?.method).toBe("DELETE");
  });

  it("words the confirmation in both languages", () => {
    expect(en.dataset.confirmDelete("Sales")).toContain("Sales");
    expect(viMessages.dataset.confirmDelete("Sales")).toContain("Sales");
    expect(viMessages.dataset.confirmDelete("Sales")).not.toBe(en.dataset.confirmDelete("Sales"));
  });
});
