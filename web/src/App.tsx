import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Api,
  ApiError,
  createLoginLink,
  errorMessage,
  fetchMe,
  redeemLink,
  signInWithKey,
  signOut,
} from "./api";
import { type Me, type StartError, sessionStarter, viewFor } from "./auth";
import { OpsDashboard } from "./OpsDashboard";
import { Workspace } from "./Workspace";

// Once per page load, whatever React does with effects (see sessionStarter).
const startSession = sessionStarter({
  location: window.location,
  replace: (path) => window.history.replaceState(null, "", path),
  redeem: redeemLink,
  me: fetchMe,
});

type State =
  | { kind: "loading" }
  | { kind: "signed-out"; notice: string | null }
  | { kind: "signed-in"; me: Me };

export function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const signedOut = useCallback((notice: string | null) => {
    setState({ kind: "signed-out", notice });
  }, []);

  useEffect(() => {
    let live = true;
    startSession()
      .then(({ me }) => live && setState({ kind: "signed-in", me }))
      .catch((caught: StartError) => {
        if (!live) return;
        if (caught.fromLink) {
          signedOut("This sign-in link was already used or has expired. Ask for a new one.");
        } else if (caught.cause instanceof ApiError && caught.cause.status === 401) {
          signedOut(null);
        } else {
          signedOut(errorMessage(caught.cause));
        }
      });
    return () => {
      live = false;
    };
  }, [signedOut]);

  const api = useMemo(
    () => new Api(() => signedOut("Your session has ended. Sign in again.")),
    [signedOut],
  );

  async function leave() {
    try {
      await signOut();
    } finally {
      signedOut(null);
    }
  }

  return (
    <div className="shell">
      <header className="topbar">
        <h1>DA-Agent</h1>
        {state.kind === "signed-in" && (
          <div className="topbar-actions">
            <span className="muted">
              {state.me.role === "operator" ? "Operator" : state.me.workspace_name}
            </span>
            <InviteButton role={state.me.role} />
            <button type="button" className="link" onClick={leave}>
              Sign out
            </button>
          </div>
        )}
      </header>
      {state.kind === "loading" && <p className="muted">Loading…</p>}
      {state.kind === "signed-out" && (
        <SignIn
          notice={state.notice}
          onSignedIn={(me) => setState({ kind: "signed-in", me })}
        />
      )}
      {state.kind === "signed-in" &&
        (viewFor(state.me) === "ops" ? <OpsDashboard api={api} /> : <Workspace api={api} />)}
    </div>
  );
}

function SignIn({ notice, onSignedIn }: { notice: string | null; onSignedIn: (me: Me) => void }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setChecking(true);
    try {
      onSignedIn(await signInWithKey(key.trim()));
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "That API key was not accepted."
          : errorMessage(caught),
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <section className="sign-in" aria-labelledby="sign-in-title">
      <h2 id="sign-in-title">Sign in</h2>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <p>
        Open the sign-in link your operator or a teammate sent you. Links work once, for 15
        minutes.
      </p>
      <details>
        <summary>Sign in with an API key instead</summary>
        <form className="row-form" onSubmit={submit}>
          <label htmlFor="api-key">Workspace API key</label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            placeholder="dak_…"
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
          <button type="submit" className="primary" disabled={!key.trim() || checking}>
            {checking ? "Checking…" : "Sign in"}
          </button>
          {error && (
            <p role="alert" className="error full">
              {error}
            </p>
          )}
        </form>
        <p className="muted small">This browser keeps a session, not the key.</p>
      </details>
    </section>
  );
}

function InviteButton({ role }: { role: Me["role"] }) {
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function create() {
    setError(null);
    setCopied(false);
    try {
      setLink((await createLoginLink()).url);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const label = role === "operator" ? "Invite an operator" : "Invite or add a device";
  return (
    <div className="invite">
      <button type="button" className="link" onClick={create}>
        {label}
      </button>
      {(link || error) && (
        <div className="invite-panel" role="dialog" aria-label="Sign-in link">
          {error ? (
            <>
              <p className="error">{error}</p>
              <button type="button" className="link" onClick={() => setError(null)}>
                Close
              </button>
            </>
          ) : (
            <>
              <p className="small">
                Anyone who opens this link within 15 minutes signs in to{" "}
                {role === "operator" ? "the operator dashboard" : "this workspace"}. It works once.
              </p>
              <input readOnly value={link ?? ""} aria-label="Sign-in link" onFocus={(e) => e.target.select()} />
              <div className="invite-actions">
                <button type="button" className="primary" onClick={copy}>
                  {copied ? "Copied" : "Copy link"}
                </button>
                <button type="button" className="link" onClick={() => setLink(null)}>
                  Close
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
