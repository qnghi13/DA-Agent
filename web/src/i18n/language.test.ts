import { describe, expect, it } from "vitest";
import { applyLanguage, messagesFor, readStoredLanguage, storeLanguage } from "./language";

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

describe("language", () => {
  it("defaults to English", () => {
    expect(readStoredLanguage(memoryStorage())).toBe("en");
  });

  it("reads a stored choice", () => {
    expect(readStoredLanguage(memoryStorage({ "da-agent-language": "vi" }))).toBe("vi");
  });

  it("ignores a value it does not know", () => {
    expect(readStoredLanguage(memoryStorage({ "da-agent-language": "fr" }))).toBe("en");
  });

  it("stores the choice", () => {
    const storage = memoryStorage();
    storeLanguage(storage, "vi");
    expect(readStoredLanguage(storage)).toBe("vi");
    storeLanguage(storage, "en");
    expect(readStoredLanguage(storage)).toBe("en");
  });

  it("survives blocked storage", () => {
    expect(readStoredLanguage(broken)).toBe("en");
    expect(() => storeLanguage(broken, "vi")).not.toThrow();
  });

  it("gives each language its dictionary and locale", () => {
    expect(messagesFor("en").locale).toBe("en-US");
    expect(messagesFor("vi").locale).toBe("vi-VN");
  });

  it("puts the language on the page for screen readers", () => {
    const doc = { documentElement: { lang: "en" } };
    applyLanguage("vi", doc);
    expect(doc.documentElement.lang).toBe("vi");
    applyLanguage("en", doc);
    expect(doc.documentElement.lang).toBe("en");
  });
});
