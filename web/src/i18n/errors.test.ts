import { describe, expect, it } from "vitest";
import { ApiError, errorDetail } from "../api";
import { en } from "./en";
import { errorMessage } from "./errors";
import { vi } from "./vi";

describe("errorMessage", () => {
  it("translates a known code", () => {
    const error = new ApiError(413, "at most 20 files", "upload.too_many_files", { max: 20 });
    const vietnamese = errorMessage(error, vi);
    const english = errorMessage(error, en);
    expect(vietnamese).toContain("20");
    expect(english).toContain("20");
    expect(vietnamese).not.toBe(english);
    expect(vietnamese).not.toBe("at most 20 files");
  });

  it("falls back to the server's words for a code it does not know", () => {
    const error = new ApiError(400, "server text", "new.thing", {});
    expect(errorMessage(error, vi)).toBe("server text");
  });

  it("falls back to the server's words when params are missing", () => {
    const error = new ApiError(413, "at most 20 files", "upload.too_many_files");
    expect(errorMessage(error, vi)).toBe("at most 20 files");
  });

  it("never shows undefined for a param of the wrong name", () => {
    const error = new ApiError(413, "at most 20 files", "upload.too_many_files", { limit: 20 });
    expect(errorMessage(error, vi)).toBe("at most 20 files");
  });

  it("wraps rate limits in a slow-down line", () => {
    const coded = new ApiError(429, "too many requests", "limit.requests", {});
    const plain = new ApiError(429, "too many requests");
    expect(errorMessage(coded, vi)).toBe(vi.errors.slowDown(vi.errors.codes["limit.requests"]({})));
    expect(errorMessage(plain, vi)).toBe(vi.errors.slowDown("too many requests"));
  });

  it("says signed out for a 401, whatever the body", () => {
    expect(errorMessage(new ApiError(401, "sign in"), vi)).toBe(vi.errors.signedOut);
  });

  it("shows other errors as they are", () => {
    expect(errorMessage(new Error("boom"), vi)).toBe("boom");
    expect(errorMessage("plain", vi)).toBe("plain");
  });
});

describe("errorDetail", () => {
  it("reads the code and params next to the detail", async () => {
    const response = new Response(
      JSON.stringify({ detail: "at most 20 files", code: "upload.too_many_files", params: { max: 20 } }),
      { status: 413 },
    );
    expect(await errorDetail(response)).toEqual({
      message: "at most 20 files",
      code: "upload.too_many_files",
      params: { max: 20 },
    });
  });

  it("reads a plain detail with no code", async () => {
    const response = new Response(JSON.stringify({ detail: "x" }), { status: 400 });
    expect(await errorDetail(response)).toEqual({ message: "x" });
  });
});
