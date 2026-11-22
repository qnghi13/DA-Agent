import { en, type Messages } from "./en";
import { vi } from "./vi";

/** Which language the UI speaks. English until the viewer picks another; the choice is a
 * per-browser convenience like the theme, so blocked or full storage is ignored. */
export type Language = "en" | "vi";

// Each name in its own language, so a person who cannot read the current one still finds theirs.
export const LANGUAGES: { value: Language; label: string }[] = [
  { value: "en", label: "English" },
  { value: "vi", label: "Tiếng Việt" },
];

const KEY = "da-agent-language";
const DICTIONARIES: Record<Language, Messages> = { en, vi };

type Storage = Pick<globalThis.Storage, "getItem" | "setItem" | "removeItem">;

export function isLanguage(value: unknown): value is Language {
  return value === "en" || value === "vi";
}

export function readStoredLanguage(storage: Storage): Language {
  try {
    const value = storage.getItem(KEY);
    return isLanguage(value) ? value : "en";
  } catch {
    return "en";
  }
}

export function storeLanguage(storage: Storage, language: Language): void {
  try {
    if (language === "en") storage.removeItem(KEY);
    else storage.setItem(KEY, language);
  } catch {
    // A private window or blocked site data: the choice lasts until the page reloads.
  }
}

export function messagesFor(language: Language): Messages {
  return DICTIONARIES[language];
}

/** `<html lang>`, so screen readers use the right voice. */
export function applyLanguage(language: Language, doc: { documentElement: { lang: string } }): void {
  doc.documentElement.lang = language;
}
