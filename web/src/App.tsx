import { KeyRound, Loader2 } from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  Api,
  ApiError,
  errorMessage,
  fetchMe,
  redeemLink,
  signInWithKey,
  signOut,
} from "./api";
import { type Me, type StartError, sessionStarter, viewFor } from "./auth";
import { AccountMenu } from "./components/account-menu";
import { Logo } from "./components/logo";
import { ThemeToggle } from "./components/theme-toggle";
import { Slash, TopBar } from "./components/top-bar";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Separator } from "./components/ui/separator";
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

  const actions =
    state.kind === "signed-in" ? (
      <>
        <ThemeToggle />
        <AccountMenu me={state.me} onSignOut={leave} />
      </>
    ) : (
      <ThemeToggle />
    );

  if (state.kind === "loading") {
    return (
      <div className="grid h-dvh place-items-center">
        <Loader2 className="text-muted-foreground size-5 animate-spin" aria-label="Loading" />
      </div>
    );
  }
  if (state.kind === "signed-out") {
    return (
      <SignIn
        notice={state.notice}
        actions={actions}
        onSignedIn={(me) => setState({ kind: "signed-in", me })}
      />
    );
  }
  if (viewFor(state.me) === "ops") {
    return (
      <div className="flex min-h-dvh flex-col">
        <TopBar
          left={
            <>
              <Logo />
              <Slash />
              <span className="truncate text-sm font-medium">Operations</span>
            </>
          }
          right={actions}
        />
        <main className="flex-1">
          <OpsDashboard api={api} />
        </main>
      </div>
    );
  }
  return <Workspace api={api} actions={actions} workspaceName={state.me.workspace_name} />;
}

function SignIn({
  notice,
  actions,
  onSignedIn,
}: {
  notice: string | null;
  actions: ReactNode;
  onSignedIn: (me: Me) => void;
}) {
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [withKey, setWithKey] = useState(false);

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
    <div className="bg-muted/40 relative flex min-h-dvh flex-col items-center justify-center px-4 py-12">
      <div className="absolute top-3 right-3">{actions}</div>
      <main className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo className="text-lg" />
        </div>
        <section
          className="bg-card rounded-xl border p-6 shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
          aria-labelledby="sign-in-title"
        >
          <h1 id="sign-in-title" className="text-xl font-semibold tracking-tight">
            Sign in
          </h1>
          <p className="text-muted-foreground mt-2 text-sm leading-relaxed">
            Open the sign-in link your operator or a teammate sent you. Links work once, for 15
            minutes.
          </p>
          {notice && (
            <p
              role="status"
              className="border-warning-line bg-warning-soft text-warning mt-4 rounded-md border px-3 py-2 text-sm"
            >
              {notice}
            </p>
          )}
          <Separator className="my-5" />
          {withKey ? (
            <form className="grid gap-3" onSubmit={submit}>
              <div className="grid gap-1.5">
                <Label htmlFor="api-key">Workspace API key</Label>
                <Input
                  id="api-key"
                  type="password"
                  autoComplete="off"
                  autoFocus
                  placeholder="dak_…"
                  className="font-mono"
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                />
              </div>
              {error && (
                <p role="alert" className="text-danger text-sm">
                  {error}
                </p>
              )}
              <Button type="submit" disabled={!key.trim() || checking}>
                {checking && <Loader2 className="animate-spin" />}
                {checking ? "Checking…" : "Sign in"}
              </Button>
              <p className="text-muted-foreground text-xs">
                This browser keeps a session, not the key.
              </p>
            </form>
          ) : (
            <Button variant="outline" className="w-full" onClick={() => setWithKey(true)}>
              <KeyRound />
              Sign in with an API key
            </Button>
          )}
        </section>
        <p className="text-muted-foreground mt-6 text-center text-xs text-balance">
          Ask questions about your spreadsheets, with answers you can trace to the query.
        </p>
      </main>
    </div>
  );
}

