import {
  ArrowUp,
  BookMarked,
  CalendarDays,
  Database,
  FilePlus2,
  Loader2,
  MessageSquarePlus,
  Square,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type Api, ApiError, type Dataset, type RunStatus } from "./api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { AddFilesDialog } from "./components/chat/add-files-dialog";
import { EvidenceContent } from "./components/chat/evidence";
import { type Evidence, RunMessage } from "./components/chat/run-message";
import { VersionList, VersionStatus, useVersionWatch } from "./components/chat/version-status";
import { DatasetSummary } from "./components/dataset-summary";
import { type MemoryDraft, MemoryPanel } from "./components/memory-panel";
import { Button } from "./components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./components/ui/sheet";
import { useMediaQuery } from "./hooks/use-media-query";
import { conversationStarts, loadConversation, saveConversation } from "./memory";
import { composerStops } from "./run";
import { suggestQuestions } from "./suggestions";
import { VersionReview } from "./VersionReview";
import { addedFiles, isOlderVersion, isWaitingForUser, versionDividers } from "./versions";

type Panel =
  | { kind: "run"; evidence: Evidence; trigger: HTMLElement | null }
  | { kind: "dataset"; trigger: HTMLElement | null }
  | { kind: "memory"; draft?: MemoryDraft; trigger: HTMLElement | null }
  | { kind: "version"; trigger: HTMLElement | null };

interface RunRef {
  id: string;
  conversation_id: string | null;
}

function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function Conversation({
  api,
  dataset,
  onRefresh,
}: {
  api: Api;
  dataset: Dataset;
  /** Called with the dataset whenever it is read again, such as while a new version is open. */
  onRefresh: (dataset: Dataset) => void;
}) {
  const m = useT();
  const datasetId = dataset.id;
  const [addOpen, setAddOpen] = useState(false);
  const [runVersions, setRunVersions] = useState<Record<string, number>>({});
  const watch = useVersionWatch(api, dataset, onRefresh);
  const openVersion = dataset.open_version ?? null;
  const shownVersion = openVersion ?? watch.outcome;
  const currentVersion = dataset.current_version ?? 1;
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
  // Each shown run's status, reported by its message; the latest one decides Send or Stop.
  const [statuses, setStatuses] = useState<Record<string, RunStatus>>({});
  const [stopping, setStopping] = useState<string | null>(null);
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
        setError(errorMessage(caught, m));
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

  const onVersion = useCallback((id: string, version: number) => {
    setRunVersions((current) => (current[id] === version ? current : { ...current, [id]: version }));
  }, []);

  // The review closes when its version is no longer waiting for a decision.
  const reviewing = panel?.kind === "version";
  const reviewable = openVersion !== null && isWaitingForUser(openVersion);
  useEffect(() => {
    if (reviewing && !reviewable) setPanel(null);
  }, [reviewing, reviewable]);

  async function loadOlder() {
    if (!olderBefore) return;
    setLoadingOlder(true);
    try {
      const page = await api.listRuns(datasetId, olderBefore);
      scrollOnNext.current = false;
      setRuns((current) => [...page.runs.map(runRef).reverse(), ...(current ?? [])]);
      setOlderBefore(page.next_before);
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setLoadingOlder(false);
    }
  }

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    // Enter while the latest question runs does nothing: the button there stops it instead.
    if (!text || sending || running) return;
    setSending(true);
    setError(null);
    try {
      const { id, conversation_id } = await askInConversation(text);
      setRuns((current) => [...(current ?? []), { id, conversation_id }]);
      setQuestion("");
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setSending(false);
    }
  }

  const onStatus = useCallback((id: string, status: RunStatus) => {
    setStatuses((current) => (current[id] === status ? current : { ...current, [id]: status }));
  }, []);

  async function stop() {
    const id = runs?.at(-1)?.id;
    if (!id || stopping) return;
    setStopping(id);
    setError(null);
    try {
      await api.cancelRun(id);
    } catch (caught) {
      setStopping(null);
      if (caught instanceof ApiError && caught.status === 409) {
        // It ended just before the click: show how it ended instead of an error.
        try {
          onStatus(id, (await api.getRun(id)).status);
        } catch {
          // The run's own message refreshes it.
        }
        return;
      }
      setError(errorMessage(caught, m));
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
  const dividers = versionDividers(
    (runs ?? []).flatMap(({ id }) => (id in runVersions ? [{ id, dataset_version: runVersions[id] }] : [])),
  );
  const last = runs?.at(-1);
  const latestId = last?.id;
  // While the latest question of this conversation runs, the send button stops it, as in other
  // chat assistants. A new conversation can always send.
  const running = composerStops(last, conversation, statuses);
  const startsFresh = last !== undefined && last.conversation_id !== conversation;
  const panelTitle =
    panel?.kind === "run"
      ? panel.evidence.run.question
      : panel?.kind === "memory"
        ? "Memory"
        : panel?.kind === "version"
          ? "Review new files"
          : panel
          ? dataset.name
          : "";
  const panelBody =
    panel?.kind === "run" ? (
      <EvidenceContent key={panel.evidence.run.id} api={api} evidence={panel.evidence} />
    ) : panel?.kind === "memory" ? (
      <MemoryPanel api={api} datasetId={datasetId} draft={panel.draft} />
    ) : panel?.kind === "version" ? (
      openVersion && reviewable ? (
        <VersionReview
          key={openVersion.version}
          api={api}
          dataset={dataset}
          version={openVersion}
          onDone={() => {
            closePanel();
            void watch.refresh().catch((caught) => setError(errorMessage(caught, m)));
          }}
        />
      ) : null
    ) : panel ? (
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <VersionList versions={watch.versions} />
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
              <EmptyState dataset={dataset} onExample={pickExample} />
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
                    {dividers.has(id) && (
                      <VersionDivider
                        version={dividers.get(id)!.to}
                        files={addedFiles(watch.versions, dividers.get(id)!.from, dividers.get(id)!.to)}
                      />
                    )}
                    {starts.has(id) && <ConversationBreak />}
                    {id in runVersions && isOlderVersion({ dataset_version: runVersions[id] }, currentVersion) && (
                      <span className="text-muted-foreground -mb-6 self-start rounded-full border px-2 py-0.5 text-[11px]">
                        data v{runVersions[id]}
                      </span>
                    )}
                    <RunMessage
                      api={api}
                      runId={id}
                      active={activeRun === id}
                      onShowWork={(evidence, trigger) =>
                        activeRun === id
                          ? closePanel()
                          : setPanel({ kind: "run", evidence, trigger })
                      }
                      onOpenMemory={(trigger) => setPanel({ kind: "memory", trigger })}
                      onStatus={onStatus}
                      onVersion={onVersion}
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
            {shownVersion && (
              <VersionStatus
                api={api}
                datasetId={datasetId}
                version={shownVersion}
                onReview={(event) => setPanel({ kind: "version", trigger: event })}
                onDismiss={watch.dismiss}
                onChange={() => void watch.refresh().catch(() => {})}
              />
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer
          inputRef={inputRef}
          question={question}
          asOf={asOf}
          sending={sending}
          running={running}
          stopping={running && stopping === latestId}
          onStop={stop}
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
              disabled={openVersion !== null}
              title={openVersion ? "Finish or discard the new files first" : undefined}
              onClick={() => setAddOpen(true)}
            >
              <FilePlus2 className="size-3.5" />
              Add files
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

      <AddFilesDialog
        api={api}
        dataset={dataset}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onUploaded={() => void watch.refresh().catch((caught) => setError(errorMessage(caught, m)))}
      />
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
                    : panel?.kind === "version"
                      ? "Choices for the files you added"
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

/** Marks where the data version changed: answers below read the newer data. */
function VersionDivider({ version, files }: { version: number; files: string[] }) {
  return (
    <div role="separator" className="text-muted-foreground flex items-center gap-3 text-xs">
      <span className="bg-border h-px flex-1" />
      <span className="max-w-[80%] text-center">
        {files.length > 0 && `Added ${files.join(", ")}. `}Answers below use data version {version}.
      </span>
      <span className="bg-border h-px flex-1" />
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

function EmptyState({ dataset, onExample }: { dataset: Dataset; onExample: (text: string) => void }) {
  const questions = useMemo(() => suggestQuestions(dataset), [dataset]);
  return (
    <div className="flex flex-col items-center gap-6 py-16 text-center">
      <div className="flex flex-col gap-2">
        <h2 className="text-2xl font-semibold tracking-tight">Ask {dataset.name} anything</h2>
        <p className="text-muted-foreground mx-auto max-w-md text-[15px] text-balance">
          Ask in your own words. Every answer comes with the queries behind it, checked against
          the data.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {questions.map((example) => (
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
  running,
  stopping,
  onStop,
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
  running: boolean;
  stopping: boolean;
  onStop: () => void;
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
          <div className="flex items-end gap-2 px-2.5 pb-2.5">
            {/* Tools wrap to a second line before they push the send button out of the box. */}
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
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
            </div>
            {running ? (
              <Button
                type="button"
                size="icon"
                className="size-8 shrink-0 rounded-full"
                disabled={stopping}
                onClick={onStop}
                aria-label={stopping ? "Stopping…" : "Stop"}
                title={stopping ? "Stopping…" : "Stop this question"}
              >
                {stopping ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Square className="size-3 fill-current" />
                )}
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                className="size-8 shrink-0 rounded-full"
                disabled={sending || !question.trim()}
                aria-label="Ask"
                title="Send (Enter) · Shift+Enter for a new line"
              >
                {sending ? <Loader2 className="animate-spin" /> : <ArrowUp />}
              </Button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
