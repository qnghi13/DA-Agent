import { AlertCircle, Check, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type Api, type Dataset, type DatasetVersion, errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import { canDiscard, isOpenVersion, isWaitingForUser, statusLine } from "@/versions";

const POLL_MS = 3000;

/**
 * Follows a dataset's versions while a chat is open. Polls the dataset every 3 s while a version is
 * open, and when it ends reads how it ended. `refresh` reads the dataset now, such as right after
 * an upload; the parent gets every read through `onRefresh`.
 */
export function useVersionWatch(api: Api, dataset: Dataset, onRefresh: (dataset: Dataset) => void) {
  const open = dataset.open_version ?? null;
  const openNumber = open?.version ?? null;
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [outcome, setOutcome] = useState<DatasetVersion | null>(null);
  const tracked = useRef<number | null>(null);
  const latest = useRef(onRefresh);
  latest.current = onRefresh;
  const datasetId = dataset.id;

  const refresh = useCallback(async () => {
    latest.current(await api.getDataset(datasetId));
  }, [api, datasetId]);

  useEffect(() => {
    if (openNumber === null) return;
    const timer = window.setInterval(() => void refresh().catch(() => {}), POLL_MS);
    return () => window.clearInterval(timer);
  }, [openNumber, refresh]);

  // The versions, for the dividers: read again whenever the data version or an open one changes.
  const current = dataset.current_version;
  useEffect(() => {
    let cancelled = false;
    api
      .listVersions(datasetId)
      .then((page) => {
        if (cancelled) return;
        setVersions(page.versions);
        if (openNumber === null && tracked.current !== null) {
          const ended = page.versions.find((v) => v.version === tracked.current);
          tracked.current = null;
          if (ended && (ended.status === "applied" || ended.status === "failed")) setOutcome(ended);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [api, datasetId, current, openNumber]);

  useEffect(() => {
    if (openNumber === null) return;
    tracked.current = openNumber;
    setOutcome(null);
  }, [openNumber]);

  return { versions, outcome, dismiss: () => setOutcome(null), refresh };
}

/**
 * The card at the end of the chat for a version that is open, or just ended. A version waiting for
 * the user opens the review; a failed or applied one can be dismissed.
 */
export function VersionStatus({
  api,
  datasetId,
  version,
  onReview,
  onDismiss,
  onChange,
}: {
  api: Api;
  datasetId: string;
  version: DatasetVersion;
  onReview: (trigger: HTMLElement) => void;
  onDismiss: () => void;
  /** Called after a discard, so the dataset is read again. */
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const open = isOpenVersion(version);
  const waiting = isWaitingForUser(version);
  const stuck = open && canDiscard(version) && !waiting;
  const failed = version.status === "failed" || stuck;

  async function discard() {
    setError(null);
    try {
      await api.discardVersion(datasetId, version.version);
      onChange();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="bg-card flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 text-sm"
    >
      {failed ? (
        <AlertCircle className="text-danger size-4 shrink-0" aria-hidden />
      ) : open && !waiting ? (
        <Loader2 className="text-info size-4 shrink-0 animate-spin" aria-hidden />
      ) : (
        <Check
          className={waiting ? "text-warning size-4 shrink-0" : "text-success size-4 shrink-0"}
          aria-hidden
        />
      )}
      <span className="min-w-0 flex-1 break-words">{statusLine(version)}</span>
      {waiting && (
        <>
          <Button size="sm" onClick={(event) => onReview(event.currentTarget)}>
            Review
          </Button>
          <Button size="sm" variant="ghost" onClick={discard}>
            Discard
          </Button>
        </>
      )}
      {stuck && (
        <Button size="sm" variant="ghost" onClick={discard}>
          Discard
        </Button>
      )}
      {!open && (
        <Button size="icon" variant="ghost" className="size-7" aria-label="Dismiss" onClick={onDismiss}>
          <X />
        </Button>
      )}
      {error && (
        <p role="alert" className="text-danger w-full text-sm">
          {error}
        </p>
      )}
    </div>
  );
}

/** The dataset's versions with their files and status, for the "About this data" panel. */
export function VersionList({ versions }: { versions: DatasetVersion[] }) {
  if (versions.length < 2) return null;
  return (
    <section className="mb-4 flex flex-col gap-2" aria-labelledby="version-list">
      <h3 id="version-list" className="text-sm font-semibold">
        Versions
      </h3>
      <ul className="divide-y rounded-lg border text-sm">
        {[...versions].reverse().map((v) => (
          <li key={v.version} className="flex flex-wrap items-baseline gap-x-3 px-3 py-2">
            <span className="font-medium">Version {v.version}</span>
            <span className="text-muted-foreground text-xs">{v.status.replace("_", " ")}</span>
            <span className="text-muted-foreground w-full text-xs break-words">
              {v.files.map((f) => f.name).join(", ")}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
