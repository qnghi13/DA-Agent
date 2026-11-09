import { describe, expect, it } from "vitest";
import { readStoredTheme, resolveTheme, storeTheme } from "./theme";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
}

const broken = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};

describe("theme", () => {
  it("follows the system until the user picks one", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("remembers the choice, and system clears it", () => {
    const storage = memoryStorage();
    storeTheme(storage, "dark");
    expect(readStoredTheme(storage)).toBe("dark");
    storeTheme(storage, "system");
    expect(readStoredTheme(storage)).toBe("system");
  });

  it("treats unknown or unreadable storage as system", () => {
    expect(readStoredTheme(memoryStorage({ "da-agent-theme": "purple" }))).toBe("system");
    expect(readStoredTheme(broken)).toBe("system");
    expect(() => storeTheme(broken, "dark")).not.toThrow();
  });
});
