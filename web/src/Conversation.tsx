import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { type Api, errorMessage } from "./api";
import { RunCard } from "./RunCard";

// The API has no run list yet, so this browser remembers which runs were asked here.
function storageKey(datasetId: string) {
  return `da-agent.runs.${datasetId}`;
}

function loadRuns(datasetId: string): string[] {
  try {
    const raw = localStorage.getItem(storageKey(datasetId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function saveRuns(datasetId: string, ids: string[]) {
  try {
    localStorage.setItem(storageKey(datasetId), JSON.stringify(ids.slice(-50)));
  } catch {
    // Storage blocked: the history lasts until the page closes.
  }
}

function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function Conversation({ api, datasetId }: { api: Api; datasetId: string }) {
  const [runs, setRuns] = useState<string[]>(() => loadRuns(datasetId));
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState(today);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [runs.length]);

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const { id } = await api.createRun(datasetId, text, asOf);
      const next = [...runs, id];
      setRuns(next);
      saveRuns(datasetId, next);
      setQuestion("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask();
    }
  }

  return (
    <div className="conversation">
      {runs.length === 0 ? (
        <div className="empty">
          <p>Ask a question about this data in your own words.</p>
          <p className="muted small">
            For example: what was the revenue in June, which product sold the most, or how many
            orders were cancelled.
          </p>
        </div>
      ) : (
        <div className="runs">
          {runs.map((id) => (
            <RunCard key={id} api={api} runId={id} />
          ))}
        </div>
      )}
      <div ref={endRef} />

      <form className="ask" onSubmit={ask}>
        <label className="visually-hidden" htmlFor="question">
          Your question
        </label>
        <textarea
          id="question"
          rows={2}
          maxLength={2000}
          placeholder="Ask a question about this data"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="ask-row">
          <label className="as-of">
            As of
            <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
          </label>
          <button type="submit" className="primary" disabled={sending || !question.trim()}>
            {sending ? "Asking…" : "Ask"}
          </button>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
