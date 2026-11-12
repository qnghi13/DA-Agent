// Dataset memory and conversations in the UI (docs/adr/0030-conversation-and-dataset-memory.md).

import type { MemoryEntry } from "./api";

export interface MemoryGroups {
  /** Suggested notes that no run reads until someone approves them. */
  review: MemoryEntry[];
  /** Approved notes that later runs read. */
  active: MemoryEntry[];
  /** Replaced or rejected notes, kept to show what changed. */
  history: MemoryEntry[];
}

export function groupMemory(entries: MemoryEntry[]): MemoryGroups {
  const groups: MemoryGroups = { review: [], active: [], history: [] };
  for (const entry of entries) {
    if (entry.superseded_by !== null || entry.status === "rejected") groups.history.push(entry);
    else if (entry.status === "proposed") groups.review.push(entry);
    else groups.active.push(entry);
  }
  return groups;
}

/** The ids of runs, oldest first, that start a conversation different from the run before. */
export function conversationStarts(
  runs: { id: string; conversation_id: string | null }[],
): Set<string> {
  const starts = new Set<string>();
  runs.forEach((run, index) => {
    if (index > 0 && run.conversation_id !== runs[index - 1].conversation_id) starts.add(run.id);
  });
  return starts;
}

export type ConversationStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const key = (datasetId: string) => `da-agent.conversation.${datasetId}`;

function browserStorage(): ConversationStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * The conversation this browser continues for a dataset. Storage is only a convenience: when it
 * is blocked or cleared, the next question starts a new conversation.
 */
export function loadConversation(
  datasetId: string,
  storage: ConversationStorage | null = browserStorage(),
): string | null {
  try {
    return storage?.getItem(key(datasetId)) ?? null;
  } catch {
    return null;
  }
}

export function saveConversation(
  datasetId: string,
  conversationId: string | null,
  storage: ConversationStorage | null = browserStorage(),
): void {
  try {
    if (conversationId) storage?.setItem(key(datasetId), conversationId);
    else storage?.removeItem(key(datasetId));
  } catch {
    // Blocked storage: the conversation lasts until the page is closed.
  }
}

export const MEMORY_KINDS: { value: MemoryEntry["kind"]; label: string; hint: string }[] = [
  {
    value: "correction",
    label: "Correction",
    hint: "A business rule the agent got wrong, such as what revenue excludes.",
  },
  { value: "alias", label: "Alias", hint: "A word people use for a column or value." },
  {
    value: "example",
    label: "Example",
    hint: "A question and the SQL that answers it correctly.",
  },
];
