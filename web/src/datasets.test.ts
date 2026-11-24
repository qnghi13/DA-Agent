import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, ApiError } from "./api";
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

  it("fails with a 404 ApiError when the dataset is already gone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "not found" }), { status: 404 })),
    );
    const error = await new Api().deleteDataset("d1").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });

  it("words the confirmation in both languages", () => {
    expect(en.dataset.confirmDelete("Sales")).toContain("Sales");
    expect(viMessages.dataset.confirmDelete("Sales")).toContain("Sales");
    expect(viMessages.dataset.confirmDelete("Sales")).not.toBe(en.dataset.confirmDelete("Sales"));
  });
});
