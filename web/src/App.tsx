import { type FormEvent, useState } from "react";
import { Api, errorMessage } from "./api";
import { Workspace } from "./Workspace";

const KEY_STORAGE = "da-agent.api-key";

function savedKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

function storeKey(key: string | null) {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    // Storage blocked: the key only lives for this page.
  }
}

export function App() {
  const [draft, setDraft] = useState(savedKey);
  const [api, setApi] = useState<Api | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function connect(event: FormEvent) {
    event.preventDefault();
    const key = draft.trim();
    const candidate = new Api(key);
    setError(null);
    setChecking(true);
    try {
      await candidate.listDatasets();
      storeKey(key);
      setApi(candidate);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setChecking(false);
    }
  }

  function disconnect() {
    storeKey(null);
    setDraft("");
    setApi(null);
  }

  return (
    <div className="shell">
      <header className="topbar">
        <h1>DA-Agent</h1>
        {api && (
          <button type="button" className="link" onClick={disconnect}>
            Disconnect
          </button>
        )}
      </header>
      {api ? (
        <Workspace api={api} />
      ) : (
        <form className="row-form" onSubmit={connect}>
          <label htmlFor="api-key">Workspace API key</label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            placeholder="dak_…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" className="primary" disabled={!draft.trim() || checking}>
            {checking ? "Checking…" : "Connect"}
          </button>
          {error && (
            <p role="alert" className="error full">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
