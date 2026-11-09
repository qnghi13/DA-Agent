/**
 * Light or dark: follows the system until the viewer picks one. The choice is a per-browser
 * convenience, so storage that is blocked or full is ignored rather than reported.
 */
export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "da-agent-theme";

type Storage = Pick<globalThis.Storage, "getItem" | "setItem" | "removeItem">;

export function resolveTheme(choice: ThemeChoice, systemDark: boolean): Theme {
  if (choice === "system") return systemDark ? "dark" : "light";
  return choice;
}

export function readStoredTheme(storage: Storage): ThemeChoice {
  try {
    const value = storage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function storeTheme(storage: Storage, choice: ThemeChoice): void {
  try {
    if (choice === "system") storage.removeItem(KEY);
    else storage.setItem(KEY, choice);
  } catch {
    // A private window or blocked site data: the choice lasts until the page reloads.
  }
}
