import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { type Api, errorMessage } from "./api";
import { RunCard } from "./RunCard";

function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function Conversation({ api, datasetId }: { api: Api; datasetId: string }) {
  // Oldest first, as a conversation reads.
  const [runs, setRuns] = useState<string[] | null>(null);
  const [olderBefore, setOlderBefore] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState(today);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollOnNext = useRef(true);

  useEffect(() => {
    let cancelled = false;
    api
      .listRuns(datasetId)
      .then((page) => {
        if (cancelled) return;
        setRuns(page.runs.map((run) => run.id).reverse());
        setOlderBefore(page.next_before);
      })
      .catch((caught) => {
        if (cancelled) return;
        setRuns([]);
        setError(errorMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [api, datasetId]);

  useEffect(() => {
    if (runs && scrollOnNext.current) endRef.current?.scrollIntoView?.({ block: "end" });
    scrollOnNext.current = true;
  }, [runs]);

  async function loadOlder() {
    if (!olderBefore) return;
    setLoadingOlder(true);
    try {
      const page = await api.listRuns(datasetId, olderBefore);
      scrollOnNext.current = false;
      setRuns((current) => [...page.runs.map((run) => run.id).reverse(), ...(current ?? [])]);
      setOlderBefore(page.next_before);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingOlder(false);
    }
  }

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const { id } = await api.createRun(datasetId, text, asOf);
      setRuns((current) => [...(current ?? []), id]);
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
      {runs === null ? (
        <p className="muted">Loading questions…</p>
      ) : runs.length === 0 ? (
        <div className="empty">
          <p>Ask a question about this data in your own words.</p>
          <p className="muted small">
            For example: what was the revenue in June, which product sold the most, or how many
            orders were cancelled.
          </p>
        </div>
      ) : (
        <div className="runs">
          {olderBefore && (
            <button
              type="button"
              className="button-quiet older"
              disabled={loadingOlder}
              onClick={loadOlder}
            >
              {loadingOlder ? "Loading…" : "Show earlier questions"}
            </button>
          )}
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
