import { ArrowUp, BookMarked, CalendarDays, Database, Loader2, MessageSquarePlus, X } from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { type Api, ApiError, type Dataset, errorMessage } from "./api";
import { EvidenceContent } from "./components/chat/evidence";
import { type Evidence, RunMessage } from "./components/chat/run-message";
import { DatasetSummary } from "./components/dataset-summary";
import { type MemoryDraft, MemoryPanel } from "./components/memory-panel";
import { Button } from "./components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./components/ui/sheet";
import { useMediaQuery } from "./hooks/use-media-query";
import { conversationStarts, loadConversation, saveConversation } from "./memory";

const EXAMPLES = [
  "What are the totals by month?",
  "Which items appear most often?",
  "What date range does the data cover?",
];

type Panel =
  | { kind: "run"; evidence: Evidence; trigger: HTMLElement | null }
  | { kind: "dataset"; trigger: HTMLElement | null }
  | { kind: "memory"; draft?: MemoryDraft; trigger: HTMLElement | null };

interface RunRef {
  id: string;
  conversation_id: string | null;
}

function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function Conversation({ api, dataset }: { api: Api; dataset: Dataset }) {
  const datasetId = dataset.id;
  // Oldest first, as a conversation reads.
  const [runs, setRuns] = useState<RunRef[] | null>(null);
  // The conversation new questions join; their runs read its earlier turns (docs/adr/0030).
  const [conversation, setConversation] = useState<string | null>(() =>
    loadConversation(datasetId),
  );
  const [olderBefore, setOlderBefore] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState(today);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollOnNext = useRef(true);
  const wide = useMediaQuery("(min-width: 1024px)");

  useEffect(() => {
    let cancelled = false;
    api
      .listRuns(datasetId)
      .then((page) => {
        if (cancelled) return;
        setRuns(page.runs.map(runRef).reverse());
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

  const panelRef = useRef(panel);
  panelRef.current = panel;
  const closePanel = useCallback(() => {
    const trigger = panelRef.current?.trigger;
    setPanel(null);
    trigger?.focus();
  }, []);

  async function loadOlder() {
    if (!olderBefore) return;
    setLoadingOlder(true);
    try {
      const page = await api.listRuns(datasetId, olderBefore);
      scrollOnNext.current = false;
      setRuns((current) => [...page.runs.map(runRef).reverse(), ...(current ?? [])]);
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
      const { id, conversation_id } = await askInConversation(text);
      setRuns((current) => [...(current ?? []), { id, conversation_id }]);
      setQuestion("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSending(false);
    }
  }

  function rememberConversation(id: string | null) {
    setConversation(id);
    saveConversation(datasetId, id);
  }

  async function askInConversation(text: string): Promise<RunRef> {
    let current = conversation;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (!current) {
        current = (await api.createConversation(datasetId)).id;
        rememberConversation(current);
      }
      try {
        const { id } = await api.createRun(datasetId, text, asOf, current);
        return { id, conversation_id: current };
      } catch (caught) {
        // A remembered conversation that is gone (or from another sign-in): start a new one.
        if (!(caught instanceof ApiError && caught.status === 404) || attempt > 0) throw caught;
        current = null;
      }
    }
    throw new Error("unreachable");
  }

  function newConversation() {
    rememberConversation(null);
    inputRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask();
    }
  }

  function pickExample(text: string) {
    setQuestion(text);
    inputRef.current?.focus();
  }

  const activeRun = panel?.kind === "run" ? panel.evidence.run.id : null;
  const starts = conversationStarts(runs ?? []);
  const last = runs?.at(-1);
  const startsFresh = last !== undefined && last.conversation_id !== conversation;
  const panelTitle =
    panel?.kind === "run"
      ? panel.evidence.run.question
      : panel?.kind === "memory"
        ? "Memory"
        : panel
          ? dataset.name
          : "";
  const panelBody =
    panel?.kind === "run" ? (
      <EvidenceContent key={panel.evidence.run.id} api={api} evidence={panel.evidence} />
    ) : panel?.kind === "memory" ? (
      <MemoryPanel api={api} datasetId={datasetId} draft={panel.draft} />
    ) : panel ? (
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <DatasetSummary dataset={dataset} />
      </div>
    ) : null;

  return (
    <div className="flex h-full min-h-0">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 pt-8 pb-8 sm:px-6">
            {runs === null ? (
              <div className="flex flex-col gap-3" aria-label="Loading questions">
                <div className="bg-muted ml-auto h-10 w-2/3 animate-pulse rounded-2xl" />
                <div className="bg-muted h-20 w-full animate-pulse rounded-xl" />
              </div>
            ) : runs.length === 0 ? (
              <EmptyState name={dataset.name} onExample={pickExample} />
            ) : (
              <>
                {olderBefore && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground self-center"
                    disabled={loadingOlder}
                    onClick={loadOlder}
                  >
                    {loadingOlder && <Loader2 className="animate-spin" />}
                    {loadingOlder ? "Loading…" : "Show earlier questions"}
                  </Button>
                )}
                {runs.map(({ id }) => (
                  <div key={id} className="flex flex-col gap-8">
                    {starts.has(id) && <ConversationBreak />}
                    <RunMessage
                      api={api}
                      runId={id}
                      active={activeRun === id}
                      onShowWork={(evidence, trigger) =>
                        activeRun === id
                          ? closePanel()
                          : setPanel({ kind: "run", evidence, trigger })
                      }
                      onCorrect={(run, trigger) =>
                        setPanel({
                          kind: "memory",
                          draft: { kind: "correction", source_run_id: run.id },
                          trigger,
                        })
                      }
                    />
                  </div>
                ))}
                {startsFresh && <ConversationBreak fresh />}
              </>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer
          inputRef={inputRef}
          question={question}
          asOf={asOf}
          sending={sending}
          error={error}
          onQuestion={setQuestion}
          onAsOf={setAsOf}
          onKeyDown={onKeyDown}
          onSubmit={ask}
          details={
            <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground h-8 gap-1.5 px-2 text-xs font-normal"
              aria-pressed={panel?.kind === "dataset"}
              onClick={(event) =>
                panel?.kind === "dataset"
                  ? closePanel()
                  : setPanel({ kind: "dataset", trigger: event.currentTarget })
              }
            >
              <Database className="size-3.5" />
              <span className="hidden @2xl:inline">About this data</span>
              <span className="@2xl:hidden">Data</span>
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground h-8 gap-1.5 px-2 text-xs font-normal"
              aria-pressed={panel?.kind === "memory"}
              onClick={(event) =>
                panel?.kind === "memory"
                  ? closePanel()
                  : setPanel({ kind: "memory", trigger: event.currentTarget })
              }
            >
              <BookMarked className="size-3.5" />
              Memory
            </Button>
            {conversation && last?.conversation_id === conversation && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-muted-foreground h-8 gap-1.5 px-2 text-xs font-normal"
                onClick={newConversation}
                title="Start over: new questions will not refer to the ones above"
              >
                <MessageSquarePlus className="size-3.5" />
                <span className="hidden @2xl:inline">New conversation</span>
                <span className="sr-only @2xl:hidden">New conversation</span>
              </Button>
            )}
            </>
          }
        />
      </div>

      {wide ? (
        panel && (
          <DesktopPanel title={panelTitle} onClose={closePanel}>
            {panelBody}
          </DesktopPanel>
        )
      ) : (
        <Sheet open={panel !== null} onOpenChange={(open) => !open && closePanel()}>
          <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-full">
            <SheetHeader className="border-b pr-12">
              <SheetTitle className="line-clamp-2 text-base">{panelTitle}</SheetTitle>
              <SheetDescription className="sr-only">
                {panel?.kind === "run"
                  ? "How the agent reached this answer"
                  : panel?.kind === "memory"
                    ? "Notes the agent follows for this dataset"
                    : "About this data"}
              </SheetDescription>
            </SheetHeader>
            {panelBody}
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}

function runRef(run: { id: string; conversation_id: string | null }): RunRef {
  return { id: run.id, conversation_id: run.conversation_id ?? null };
}

/** Marks where a new conversation starts: the agent does not remember questions above it. */
function ConversationBreak({ fresh = false }: { fresh?: boolean }) {
  return (
    <div role="separator" className="text-muted-foreground flex items-center gap-3 text-xs">
      <span className="bg-border h-px flex-1" />
      {fresh ? "New conversation · earlier questions are not remembered" : "New conversation"}
      <span className="bg-border h-px flex-1" />
    </div>
  );
}

function DesktopPanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, [title]);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside
      aria-label="Details"
      className="bg-background animate-in slide-in-from-right-4 fade-in flex w-[min(560px,45vw)] shrink-0 flex-col border-l duration-200"
    >
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <h2
          ref={headingRef}
          tabIndex={-1}
          className="min-w-0 flex-1 truncate text-sm font-medium outline-none"
        >
          {title}
        </h2>
        <Button variant="ghost" size="icon" className="size-8" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </header>
      {children}
    </aside>
  );
}

function EmptyState({ name, onExample }: { name: string; onExample: (text: string) => void }) {
  return (
    <div className="flex flex-col items-center gap-6 py-16 text-center">
      <div className="flex flex-col gap-2">
        <h2 className="text-2xl font-semibold tracking-tight">Ask {name} anything</h2>
        <p className="text-muted-foreground mx-auto max-w-md text-[15px] text-balance">
          Ask in your own words. Every answer comes with the queries behind it, checked against
          the data.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => onExample(example)}
            className="hover:bg-accent focus-visible:ring-ring/50 rounded-full border px-3.5 py-1.5 text-sm transition-colors outline-none focus-visible:ring-[3px]"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer({
  inputRef,
  question,
  asOf,
  sending,
  error,
  onQuestion,
  onAsOf,
  onKeyDown,
  onSubmit,
  details,
}: {
  details: ReactNode;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  question: string;
  asOf: string;
  sending: boolean;
  error: string | null;
  onQuestion: (text: string) => void;
  onAsOf: (date: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <div className="bg-background/80 shrink-0 px-4 pb-4 backdrop-blur sm:px-6">
      <form onSubmit={onSubmit} className="@container mx-auto w-full max-w-3xl">
        {error && (
          <p role="alert" className="text-danger mb-2 text-sm">
            {error}
          </p>
        )}
        <div className="bg-card focus-within:border-ring/60 focus-within:ring-ring/20 rounded-2xl border shadow-[0_2px_12px_rgba(0,0,0,0.05)] transition-shadow focus-within:ring-[3px]">
          <label className="sr-only" htmlFor="question">
            Your question
          </label>
          <textarea
            ref={inputRef}
            id="question"
            rows={1}
            maxLength={2000}
            placeholder="Ask a question about this data…"
            value={question}
            onChange={(event) => onQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            className="placeholder:text-muted-foreground block max-h-48 min-h-[52px] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] outline-none field-sizing-content"
          />
          <div className="flex items-center gap-2 px-2.5 pb-2.5">
            <label className="text-muted-foreground hover:bg-accent flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs whitespace-nowrap">
              <CalendarDays className="size-3.5" aria-hidden />
              <span>As of</span>
              <input
                type="date"
                value={asOf}
                onChange={(event) => onAsOf(event.target.value)}
                className="text-foreground bg-transparent font-mono text-xs outline-none dark:[color-scheme:dark]"
              />
            </label>
            {details}
            <span className="text-muted-foreground ml-auto hidden text-xs whitespace-nowrap @3xl:inline">
              Enter to send · Shift+Enter for a new line
            </span>
            <Button
              type="submit"
              size="icon"
              className="ml-auto size-8 shrink-0 rounded-full @3xl:ml-0"
              disabled={sending || !question.trim()}
              aria-label="Ask"
            >
              {sending ? <Loader2 className="animate-spin" /> : <ArrowUp />}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}
