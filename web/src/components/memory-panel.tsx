import { Check, ChevronRight, Loader2, Pencil, Trash2, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { type Api, type MemoryEntry, type NewMemory, errorMessage } from "@/api";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { MEMORY_KINDS, groupMemory } from "@/memory";

/** What a "Correct" button on an answer fills in. */
export type MemoryDraft = Partial<NewMemory>;

/**
 * Notes people teach the agent about one dataset (docs/adr/0030). A note someone writes here is
 * in use at once; notes the agent suggests wait for review. Only new questions read them.
 */
export function MemoryPanel({
  api,
  datasetId,
  draft,
}: {
  api: Api;
  datasetId: string;
  draft?: MemoryDraft;
}) {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setEntries((await api.listMemory(datasetId)).memory);
    } catch (caught) {
      setError(errorMessage(caught));
      setEntries((current) => current ?? []);
    }
  }, [api, datasetId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function change(action: () => Promise<unknown>): Promise<boolean> {
    setError(null);
    let ok = true;
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(caught));
      ok = false;
    }
    await reload();
    return ok;
  }

  const groups = groupMemory(entries ?? []);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-4">
      <NoteForm
        key={JSON.stringify(draft ?? {})}
        draft={draft}
        onSave={(note) => change(() => api.writeMemory(datasetId, note))}
      />
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
      {entries === null ? (
        <div className="bg-muted h-24 animate-pulse rounded-lg" aria-label="Loading notes" />
      ) : (
        <>
          {groups.review.length > 0 && (
            <Group title={`Waiting for review (${groups.review.length})`}>
              {groups.review.map((entry) => (
                <NoteCard key={entry.id} entry={entry}>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => change(() => api.reviewMemory(entry.id, "approve"))}
                  >
                    <Check />
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => change(() => api.reviewMemory(entry.id, "reject"))}
                  >
                    <X />
                    Reject
                  </Button>
                </NoteCard>
              ))}
            </Group>
          )}
          <Group title={`In use (${groups.active.length})`}>
            {groups.active.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No notes yet. When an answer uses the wrong rule, correct it here and new questions
                will follow the note.
              </p>
            ) : (
              groups.active.map((entry) => (
                <ActiveNote
                  key={entry.id}
                  entry={entry}
                  onEdit={(text, sql) => change(() => api.editMemory(entry.id, text, sql))}
                  onDelete={() => change(() => api.deleteMemory(entry.id))}
                />
              ))
            )}
          </Group>
          {groups.history.length > 0 && (
            <details className="group rounded-lg border">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium">
                <ChevronRight className="size-4 transition-transform group-open:rotate-90" />
                History ({groups.history.length})
              </summary>
              <div className="flex flex-col gap-2 border-t p-3">
                {groups.history.map((entry) => (
                  <NoteCard key={entry.id} entry={entry} muted />
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function NoteForm({
  draft,
  onSave,
}: {
  draft?: MemoryDraft;
  onSave: (note: NewMemory) => Promise<boolean>;
}) {
  const [kind, setKind] = useState<NewMemory["kind"]>(draft?.kind ?? "correction");
  const [text, setText] = useState(draft?.text ?? "");
  const [sql, setSql] = useState(draft?.sql ?? "");
  const [saving, setSaving] = useState(false);
  const needsSql = kind === "example";
  const ready = text.trim() !== "" && (!needsSql || sql.trim() !== "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    const saved = await onSave({
      kind,
      text: text.trim(),
      ...(needsSql ? { sql: sql.trim() } : {}),
      ...(draft?.source_run_id ? { source_run_id: draft.source_run_id } : {}),
    });
    setSaving(false);
    if (saved) {
      setText("");
      setSql("");
    }
  }

  const hint = MEMORY_KINDS.find((item) => item.value === kind)?.hint;
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-xl border p-3">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">Teach the agent</h3>
        <p className="text-muted-foreground text-xs leading-relaxed">
          Notes apply to new questions about this dataset, for everyone in the workspace. A note
          wins over the dataset's documents where they disagree.
        </p>
      </div>
      <div role="radiogroup" aria-label="Kind of note" className="flex flex-wrap gap-1.5">
        {MEMORY_KINDS.map((item) => (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={kind === item.value}
            onClick={() => setKind(item.value)}
            className={cn(
              "focus-visible:ring-ring/50 rounded-full border px-3 py-1 text-xs transition-colors outline-none focus-visible:ring-[3px]",
              kind === item.value ? "bg-foreground text-background" : "hover:bg-accent",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      <label className="sr-only" htmlFor="memory-text">
        {needsSql ? "Question" : "Note"}
      </label>
      <Textarea
        id="memory-text"
        maxLength={2000}
        placeholder={
          needsSql ? "The question, such as: Revenue in October 2025" : "Revenue excludes cancelled orders."
        }
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {needsSql && (
        <>
          <label className="sr-only" htmlFor="memory-sql">
            SQL that answers it
          </label>
          <Textarea
            id="memory-sql"
            maxLength={10000}
            placeholder="SELECT …"
            className="font-mono text-xs md:text-xs"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
          />
        </>
      )}
      {draft?.source_run_id && (
        <p className="text-muted-foreground text-xs">Linked to the answer you are correcting.</p>
      )}
      <Button type="submit" size="sm" className="self-end" disabled={!ready || saving}>
        {saving && <Loader2 className="animate-spin" />}
        Save note
      </Button>
    </form>
  );
}

function ActiveNote({
  entry,
  onEdit,
  onDelete,
}: {
  entry: MemoryEntry;
  onEdit: (text: string, sql: string | null) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(entry.text);
  const [sql, setSql] = useState(entry.sql ?? "");

  if (editing) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3">
        <Textarea
          aria-label="Note"
          maxLength={2000}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        {entry.kind === "example" && (
          <Textarea
            aria-label="SQL"
            maxLength={10000}
            className="font-mono text-xs md:text-xs"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
          />
        )}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!text.trim()}
            onClick={() => void onEdit(text.trim(), entry.kind === "example" ? sql.trim() : null)}
          >
            Save
          </Button>
        </div>
      </div>
    );
  }
  return (
    <NoteCard entry={entry}>
      <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
        <Pencil />
        Edit
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="text-muted-foreground"
        onClick={() => {
          if (window.confirm("Delete this note? New questions will no longer follow it.")) {
            void onDelete();
          }
        }}
      >
        <Trash2 />
        Delete
      </Button>
    </NoteCard>
  );
}

const KIND_LABEL = Object.fromEntries(MEMORY_KINDS.map((item) => [item.value, item.label]));

function NoteCard({
  entry,
  muted = false,
  children,
}: {
  entry: MemoryEntry;
  muted?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-2 rounded-lg border px-3 py-2.5", muted && "opacity-70")}>
      <div className="flex items-center gap-2">
        <StatusBadge tone={entry.kind === "correction" ? "warning" : "info"}>
          {KIND_LABEL[entry.kind]}
        </StatusBadge>
        {muted && (
          <span className="text-muted-foreground text-xs">
            {entry.superseded_by ? "Replaced" : "Rejected"}
          </span>
        )}
        <span className="text-muted-foreground ml-auto truncate font-mono text-[11px]">
          {entry.author}
        </span>
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-wrap">{entry.text}</p>
      {entry.sql && (
        <pre className="bg-muted overflow-x-auto rounded-md p-2 font-mono text-xs leading-relaxed">
          {entry.sql}
        </pre>
      )}
      {children && <div className="flex flex-wrap justify-end gap-1">{children}</div>}
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-muted-foreground text-xs font-medium">{title}</h3>
      {children}
    </section>
  );
}
