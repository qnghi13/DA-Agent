import { AlertTriangle, Check, ChevronRight, Loader2, Pencil, Trash2, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { type Api, type MemoryEntry, type NewMemory } from "@/api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { MEMORY_KINDS, groupMemory } from "@/memory";
import { MAX_NAME, metricSentence, readMetricName } from "@/metrics";

/** What the Correct button on an answer fills in. */
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
  const m = useT();
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setEntries((await api.listMemory(datasetId)).memory);
    } catch (caught) {
      setError(errorMessage(caught, m));
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
      setError(errorMessage(caught, m));
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
        <div className="bg-muted h-24 animate-pulse rounded-lg" aria-label={m.memory.loading} />
      ) : (
        <>
          <Group title={m.memory.definitions(groups.definitions.length)}>
            {groups.definitions.length === 0 ? (
              <p className="text-muted-foreground text-sm">{m.memory.noDefinitions}</p>
            ) : (
              groups.definitions.map((entry) => (
                <DefinitionCard
                  key={entry.id}
                  entry={entry}
                  onRename={(name, aliases) => change(() => api.renameMetric(entry.id, name, aliases))}
                  onDelete={() => change(() => api.deleteMemory(entry.id))}
                />
              ))
            )}
          </Group>
          {groups.review.length > 0 && (
            <Group title={m.memory.waiting(groups.review.length)}>
              {groups.review.map((entry) => (
                <NoteCard key={entry.id} entry={entry}>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => change(() => api.reviewMemory(entry.id, "approve"))}
                  >
                    <Check />
                    {m.spec.approve}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => change(() => api.reviewMemory(entry.id, "reject"))}
                  >
                    <X />
                    {m.memory.reject}
                  </Button>
                </NoteCard>
              ))}
            </Group>
          )}
          <Group title={m.memory.inUse(groups.active.length)}>
            {groups.active.length === 0 ? (
              <p className="text-muted-foreground text-sm">{m.memory.noNotes}</p>
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
                {m.memory.history(groups.history.length)}
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
  const m = useT();
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

  const hint = m.memory.kinds[kind].hint;
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-xl border p-3">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">{m.memory.teach}</h3>
        <p className="text-muted-foreground text-xs leading-relaxed">{m.memory.teachText}</p>
      </div>
      <div role="radiogroup" aria-label={m.memory.kindLabel} className="flex flex-wrap gap-1.5">
        {MEMORY_KINDS.map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={kind === value}
            onClick={() => setKind(value)}
            className={cn(
              "focus-visible:ring-ring/50 rounded-full border px-3 py-1 text-xs transition-colors outline-none focus-visible:ring-[3px]",
              kind === value ? "bg-foreground text-background" : "hover:bg-accent",
            )}
          >
            {m.memory.kinds[value].label}
          </button>
        ))}
      </div>
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      <label className="sr-only" htmlFor="memory-text">
        {needsSql ? m.memory.question : m.feedback.note}
      </label>
      <Textarea
        id="memory-text"
        maxLength={2000}
        placeholder={needsSql ? m.memory.questionPlaceholder : m.memory.notePlaceholder}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {needsSql && (
        <>
          <label className="sr-only" htmlFor="memory-sql">
            {m.memory.sqlLabel}
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
        <p className="text-muted-foreground text-xs">{m.memory.linked}</p>
      )}
      <Button type="submit" size="sm" className="self-end" disabled={!ready || saving}>
        {saving && <Loader2 className="animate-spin" />}
        {m.memory.saveNote}
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
  const m = useT();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(entry.text);
  const [sql, setSql] = useState(entry.sql ?? "");

  if (editing) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3">
        <Textarea
          aria-label={m.feedback.note}
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
            {m.common.cancel}
          </Button>
          <Button
            size="sm"
            disabled={!text.trim()}
            onClick={() => void onEdit(text.trim(), entry.kind === "example" ? sql.trim() : null)}
          >
            {m.common.save}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <NoteCard entry={entry}>
      <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
        <Pencil />
        {m.evidence.edit}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="text-muted-foreground"
        onClick={() => {
          if (window.confirm(m.memory.confirmDeleteNote)) {
            void onDelete();
          }
        }}
      >
        <Trash2 />
        {m.memory.delete}
      </Button>
    </NoteCard>
  );
}

function DefinitionCard({
  entry,
  onRename,
  onDelete,
}: {
  entry: MemoryEntry;
  onRename: (name: string, aliases: string[]) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}) {
  const m = useT();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(entry.name ?? "");
  const [aliases, setAliases] = useState((entry.aliases ?? []).join(", "));
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const read = readMetricName(name, aliases, m);
    if ("error" in read) {
      setError(read.error);
      return;
    }
    setError(null);
    if (await onRename(read.name, read.aliases)) setEditing(false);
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3">
        <Input aria-label={m.spec.name} maxLength={MAX_NAME} value={name} onChange={(event) => setName(event.target.value)} />
        <Input
          aria-label={m.metricDialog.aliases}
          placeholder={m.metricDialog.aliases}
          value={aliases}
          onChange={(event) => setAliases(event.target.value)}
        />
        {error && (
          <p role="alert" className="text-danger text-xs">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            {m.common.cancel}
          </Button>
          <Button size="sm" onClick={() => void save()}>
            {m.common.save}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <NoteCard entry={entry}>
      <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
        <Pencil />
        {m.memory.rename}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="text-muted-foreground"
        onClick={() => {
          if (window.confirm(m.memory.confirmDeleteDefinition)) {
            void onDelete();
          }
        }}
      >
        <Trash2 />
        {m.memory.delete}
      </Button>
    </NoteCard>
  );
}

function NoteCard({
  entry,
  muted = false,
  children,
}: {
  entry: MemoryEntry;
  muted?: boolean;
  children?: React.ReactNode;
}) {
  const m = useT();
  const kinds: Record<string, string> = {
    ...Object.fromEntries(MEMORY_KINDS.map((value) => [value, m.memory.kinds[value].label])),
    metric: m.memory.definition,
  };
  return (
    <div className={cn("flex flex-col gap-2 rounded-lg border px-3 py-2.5", muted && "opacity-70")}>
      <div className="flex items-center gap-2">
        <StatusBadge tone={entry.kind === "correction" ? "warning" : entry.kind === "metric" ? "success" : "info"}>
          {kinds[entry.kind] ?? entry.kind}
        </StatusBadge>
        {muted && (
          <span className="text-muted-foreground text-xs">
            {entry.superseded_by ? m.memory.replaced : m.memory.rejected}
          </span>
        )}
        <span className="text-muted-foreground ml-auto truncate font-mono text-[11px]">
          {entry.author}
        </span>
      </div>
      {entry.kind === "metric" ? (
        <MetricBody entry={entry} />
      ) : (
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{entry.text}</p>
      )}
      {entry.sql && (
        <pre className="bg-muted overflow-x-auto rounded-md p-2 font-mono text-xs leading-relaxed">
          {entry.sql}
        </pre>
      )}
      {children && <div className="flex flex-wrap justify-end gap-1">{children}</div>}
    </div>
  );
}

function MetricBody({ entry }: { entry: MemoryEntry }) {
  const m = useT();
  return (
    <div className="flex flex-col gap-1">
      <p className="text-sm font-medium">
        {entry.name}
        {entry.aliases && entry.aliases.length > 0 && (
          <span className="text-muted-foreground font-normal">{m.memory.also(entry.aliases.join(", "))}</span>
        )}
      </p>
      <p className="text-sm leading-relaxed">{entry.facts ? metricSentence(entry.facts, m) : entry.text}</p>
      {entry.stale && entry.superseded_by === null && (
        <p className="text-warning flex items-start gap-1.5 text-xs">
          <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
          {m.memory.stale}
        </p>
      )}
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
