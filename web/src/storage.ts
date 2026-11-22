/** Per-browser conveniences (theme, language) read and write through this. With site data blocked,
 * reading `window.localStorage` itself throws; the page then keeps choices in memory for the
 * session instead of failing to render. */

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const values = new Map<string, string>();
const inMemory: StorageLike = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => void values.set(key, value),
  removeItem: (key) => void values.delete(key),
};

export function browserStorage(read: () => StorageLike = () => window.localStorage): StorageLike {
  try {
    return read();
  } catch {
    return inMemory;
  }
}
