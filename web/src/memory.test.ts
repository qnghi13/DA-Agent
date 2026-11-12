import { describe, expect, it } from "vitest";
import type { MemoryEntry } from "./api";
import {
  conversationStarts,
  groupMemory,
  loadConversation,
  saveConversation,
  type ConversationStorage,
} from "./memory";

const entry = (id: string, status: MemoryEntry["status"], superseded_by: string | null = null): MemoryEntry => ({
  id,
  kind: "correction",
  text: `note ${id}`,
  sql: null,
  status,
  author: "key:a",
  source_run_id: null,
  superseded_by,
});

describe("groupMemory", () => {
  it("splits suggestions to review, notes in use, and history", () => {
    const groups = groupMemory([
      entry("a", "approved"),
      entry("b", "proposed"),
      entry("c", "approved", "a"),
      entry("d", "rejected"),
    ]);
    expect(groups.review.map((e) => e.id)).toEqual(["b"]);
    expect(groups.active.map((e) => e.id)).toEqual(["a"]);
    expect(groups.history.map((e) => e.id)).toEqual(["c", "d"]);
  });
});

describe("conversationStarts", () => {
  it("marks each run that starts a different conversation from the one before it", () => {
    const starts = conversationStarts([
      { id: "1", conversation_id: null },
      { id: "2", conversation_id: "x" },
      { id: "3", conversation_id: "x" },
      { id: "4", conversation_id: "y" },
      { id: "5", conversation_id: null },
    ]);
    expect([...starts]).toEqual(["2", "4", "5"]);
  });

  it("does not mark the first run", () => {
    expect(conversationStarts([{ id: "1", conversation_id: "x" }]).size).toBe(0);
  });
});

function memoryStorage(): ConversationStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
}

describe("conversation storage", () => {
  it("remembers the current conversation per dataset", () => {
    const storage = memoryStorage();
    saveConversation("d1", "c1", storage);
    expect(loadConversation("d1", storage)).toBe("c1");
    expect(loadConversation("d2", storage)).toBeNull();
    saveConversation("d1", null, storage);
    expect(loadConversation("d1", storage)).toBeNull();
  });

  it("works without storage, such as in a private window", () => {
    const broken: ConversationStorage = {
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
    expect(() => saveConversation("d1", "c1", broken)).not.toThrow();
    expect(loadConversation("d1", broken)).toBeNull();
  });
});
