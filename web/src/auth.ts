// Browser sign-in (docs/adr/0025): a one-time link puts a token in the URL fragment, which the
// server never sees in its logs; the UI posts it once and gets an HttpOnly session cookie.

export interface Me {
  role: "member" | "operator";
  workspace_id: string | null;
  workspace_name: string | null;
}

const TOKEN = /^#token=(dal_[A-Za-z0-9_-]{43})$/;

/** The token of a login link, if this page was opened from one. */
export function loginToken(location: { pathname: string; hash: string }): string | null {
  if (location.pathname !== "/login") return null;
  return TOKEN.exec(location.hash)?.[1] ?? null;
}

/** Which screen a signed-in caller sees. */
export function viewFor(me: Me): "ops" | "workspace" {
  return me.role === "operator" ? "ops" : "workspace";
}

export interface Started {
  me: Me;
  fromLink: boolean;
}

/** Why the page could not start signed in; `fromLink` tells a spent link from no session. */
export class StartError extends Error {
  constructor(
    readonly cause: unknown,
    readonly fromLink: boolean,
  ) {
    super(cause instanceof Error ? cause.message : String(cause));
  }
}

/**
 * Starts the session once per page load. React may run the start effect twice (StrictMode, a
 * remount); a second run must not ask "who am I" before the first has set the cookie, or redeem
 * the same one-time link again. Both runs share one promise.
 */
export function sessionStarter(deps: {
  location: { pathname: string; hash: string };
  replace: (path: string) => void;
  redeem: (token: string) => Promise<Me>;
  me: () => Promise<Me>;
}): () => Promise<Started> {
  let started: Promise<Started> | null = null;
  return () => {
    if (started) return started;
    const token = loginToken(deps.location);
    // Drop the token from the address bar and history before anything else happens.
    if (deps.location.pathname === "/login") deps.replace("/");
    const fromLink = token !== null;
    started = (token ? deps.redeem(token) : deps.me()).then(
      (me) => ({ me, fromLink }),
      (caught) => {
        throw new StartError(caught, fromLink);
      },
    );
    return started;
  };
}
