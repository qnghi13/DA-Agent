import { describe, expect, it } from "vitest";
import { browserStorage } from "./storage";

describe("browserStorage", () => {
  it("is the browser's storage when it can be read", () => {
    const real = { getItem: () => "x", setItem: () => {}, removeItem: () => {} };
    expect(browserStorage(() => real)).toBe(real);
  });

  it("falls back to storage in memory when reading localStorage itself throws", () => {
    const blocked = () => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    };
    const storage = browserStorage(blocked);
    storage.setItem("da-agent-language", "vi");
    expect(storage.getItem("da-agent-language")).toBe("vi");
    // The same fallback for every caller, so a choice lasts for the session.
    expect(browserStorage(blocked).getItem("da-agent-language")).toBe("vi");
    storage.removeItem("da-agent-language");
    expect(storage.getItem("da-agent-language")).toBeNull();
  });
});
