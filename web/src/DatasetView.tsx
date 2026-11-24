import { AlertCircle, ArrowRight, Check, ChevronRight, Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type Api, ApiError, BUSY_STATUSES, type Dataset, type DatasetSpec } from "./api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { Conversation } from "./Conversation";
import { DatasetSummary } from "./components/dataset-summary";
import { SetupPage } from "./components/setup/stepper";
import { StatusBadge } from "./components/status-badge";
import { Button } from "./components/ui/button";
import { Skeleton } from "./components/ui/skeleton";
import { SpecEditor } from "./SpecEditor";
import { datasetStatus } from "./status";

const POLL_MS = 2000;

export function DatasetView(
  props: Parameters<typeof DatasetViewBody>[0] & { onDeleted: () => void },
) {
  const m = useT();
  const { api, datasetId, onDeleted } = props;
  const [name, setName] = useState<string | null>(null);
  const { onChange } = props;
  const handleChange = useCallback(
    (dataset: Dataset) => {
      setName(dataset.name);
      onChange(dataset);
    },
    [onChange],
  );
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function remove() {
    if (name === null || !window.confirm(m.dataset.confirmDelete(name))) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteDataset(datasetId);
      onDeleted();
    } catch (caught) {
      // Another tab already deleted it: the dataset is gone, which is what was asked.
      if (caught instanceof ApiError && caught.status === 404) {
        onDeleted();
        return;
      }
      setDeleteError(errorMessage(caught, m));
      setDeleting(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-end gap-3 border-b px-4 py-1.5">
        {deleteError && (
          <p role="alert" className="text-danger mr-auto text-sm">
            {deleteError}
          </p>
        )}
        <Button variant="ghost" size="sm" disabled={deleting || name === null} onClick={() => void remove()}>
          <Trash2 />
          {m.dataset.delete}
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <DatasetViewBody
          api={api}
          datasetId={datasetId}
          onChange={handleChange}
        />
      </div>
    </div>
  );
}

function DatasetViewBody({
  api,
  datasetId,
  onChange,
}: {
  api: Api;
  datasetId: string;
  onChange: (dataset: Dataset) => void;
}) {
  const m = useT();
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  // Bumped after an approval so the poll starts again.
  const [version, setVersion] = useState(0);
  // A dataset that turns ready while open here shows a short confirmation before the chat.
  const firstStatus = useRef<Dataset["status"] | null>(null);
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function load() {
      try {
        const next = await api.getDataset(datasetId);
        if (cancelled) return;
        firstStatus.current ??= next.status;
        setDataset(next);
        onChange(next);
        setError(null);
        if (BUSY_STATUSES.includes(next.status)) timer = window.setTimeout(load, POLL_MS);
      } catch (caught) {
        if (!cancelled) setError(errorMessage(caught, m));
      }
    }
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, datasetId, onChange, version]);

  async function approve(spec?: DatasetSpec) {
    setApproving(true);
    setError(null);
    try {
      await api.approveDataset(datasetId, spec);
      setVersion((v) => v + 1);
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setApproving(false);
    }
  }

  const becameReady = firstStatus.current !== null && firstStatus.current !== "ready";

  if (!dataset) {
    return error ? (
      <p role="alert" className="text-danger p-6 text-sm">
        {error}
      </p>
    ) : (
      <div className="grid h-full place-items-center">
        <Loader2 className="text-muted-foreground size-5 animate-spin" aria-label={m.common.loading} />
      </div>
    );
  }

  if (dataset.status === "ready" && !(becameReady && !chatOpen)) {
    return (
      <Conversation
        api={api}
        dataset={dataset}
        onRefresh={(next) => {
          setDataset(next);
          onChange(next);
        }}
      />
    );
  }
  if (dataset.status === "ready") {
    return (
      <SetupPage step={3} title={m.dataset.isReady(dataset.name)}>
        <div className="bg-card flex flex-col items-start gap-4 rounded-xl border p-6">
          <div className="border-success-line bg-success-soft text-success grid size-10 place-items-center rounded-full border">
            <Check className="size-5" aria-hidden />
          </div>
          <p className="text-muted-foreground max-w-xl text-[15px] leading-relaxed">
            {m.dataset.readyText}
          </p>
          <Button onClick={() => setChatOpen(true)} autoFocus>
            {m.dataset.ask}
            <ArrowRight />
          </Button>
        </div>
      </SetupPage>
    );
  }

  const status = datasetStatus(dataset.status, m);
  return (
    <SetupPage
      step={2}
      title={
        <span className="flex flex-wrap items-center gap-3">
          {dataset.name}
          <StatusBadge tone={status.tone} busy={status.busy}>
            {status.label}
          </StatusBadge>
        </span>
      }
      description={m.dataset.statusText[dataset.status]}
    >
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
      {dataset.status === "failed" && (
        <div className="border-danger-line bg-danger-soft flex gap-3 rounded-lg border px-4 py-3 text-sm">
          <AlertCircle className="text-danger mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="whitespace-pre-wrap">{dataset.error ?? m.dataset.noDetails}</div>
        </div>
      )}
      {status.busy && (
        <div className="flex flex-col gap-3" aria-hidden>
          <div className="bg-card flex flex-col gap-3 rounded-xl border p-5">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-2/3" />
          </div>
        </div>
      )}
      {dataset.status === "proposed" && dataset.spec && (
        <SpecEditor spec={dataset.spec} busy={approving} onApprove={approve} />
      )}
      {!status.busy && (
        <details className="group bg-card rounded-xl border" open={dataset.status === "failed"}>
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium">
            <ChevronRight className="text-muted-foreground size-4 transition-transform group-open:rotate-90" />
            {m.dataset.filesAndReport}
          </summary>
          <div className="border-t p-4">
            <DatasetSummary dataset={dataset} showSpec={false} />
          </div>
        </details>
      )}
    </SetupPage>
  );
}

