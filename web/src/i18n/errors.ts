import { ApiError } from "../api";
import type { Messages, Params } from "./en";

class MissingParam extends Error {}

/** Params that refuse a name the server did not send, so a message never says "undefined". */
function strict(params: Record<string, unknown>): Params {
  return new Proxy({} as Params, {
    get(_, name) {
      const value = typeof name === "string" ? params[name] : undefined;
      if (value === undefined || value === null) throw new MissingParam();
      return String(value);
    },
  });
}

function translated(error: ApiError, m: Messages): string {
  if (!error.code || !error.params) return error.message;
  const codes = m.errors.codes as Record<string, ((p: Params) => string) | undefined>;
  const translate = Object.hasOwn(codes, error.code) ? codes[error.code] : undefined;
  if (!translate) return error.message;
  try {
    return translate(strict(error.params));
  } catch (caught) {
    if (caught instanceof MissingParam) return error.message;
    throw caught;
  }
}

/** What to show for a failed call: the API's code in the UI language when the web knows it, the
 * server's own words otherwise. */
export function errorMessage(caught: unknown, m: Messages): string {
  if (caught instanceof ApiError && caught.status === 401) return m.errors.signedOut;
  if (caught instanceof ApiError) {
    const text = translated(caught, m);
    return caught.status === 429 ? m.errors.slowDown(text) : text;
  }
  return caught instanceof Error ? caught.message : String(caught);
}
