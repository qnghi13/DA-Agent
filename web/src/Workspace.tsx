import { Menu, PanelLeft, Plus } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { type Api, type Dataset, errorMessage } from "./api";
import { DatasetView } from "./DatasetView";
import { Logo } from "./components/logo";
import { StatusBadge } from "./components/status-badge";
import { Slash, TopBar } from "./components/top-bar";
import { Button } from "./components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "./components/ui/tooltip";
import { cn } from "./lib/utils";
import { datasetStatus } from "./status";
import { UploadForm } from "./UploadForm";

type Selection = { kind: "upload" } | { kind: "dataset"; id: string };

export function Workspace({
  api,
  actions,
  workspaceName,
}: {
  api: Api;
  actions: ReactNode;
  workspaceName: string | null;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selection, setSelection] = useState<Selection>({ kind: "upload" });
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { datasets } = await api.listDatasets();
      setDatasets(datasets);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Keep the list's status badges in step with the open dataset.
  const onDatasetChange = useCallback((changed: Dataset) => {
    setDatasets((current) => current.map((d) => (d.id === changed.id ? changed : d)));
  }, []);

  function select(next: Selection) {
    setSelection(next);
    setDrawerOpen(false);
  }

  const current =
    selection.kind === "dataset" ? datasets.find((d) => d.id === selection.id) : undefined;
  const status = current && datasetStatus(current.status);

  const list = (
    <DatasetList
      datasets={datasets}
      selection={selection}
      error={error}
      onSelect={select}
    />
  );

  return (
    <div className="flex h-dvh flex-col">
      <TopBar
        left={
          <>
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              aria-label="Open datasets"
              onClick={() => setDrawerOpen(true)}
            >
              <Menu />
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="hidden md:inline-flex"
                  aria-label={sidebarOpen ? "Hide datasets" : "Show datasets"}
                  aria-expanded={sidebarOpen}
                  onClick={() => setSidebarOpen((open) => !open)}
                >
                  <PanelLeft />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{sidebarOpen ? "Hide datasets" : "Show datasets"}</TooltipContent>
            </Tooltip>
            <Logo className="hidden sm:inline-flex" />
            <span className="hidden sm:inline">
              <Slash />
            </span>
            {workspaceName && (
              <>
                <span className="text-muted-foreground hidden truncate text-sm lg:inline">
                  {workspaceName}
                </span>
                <span className="hidden lg:inline">
                  <Slash />
                </span>
              </>
            )}
            <span className="truncate text-sm font-medium">
              {current ? current.name : "New dataset"}
            </span>
            {status && (
              <StatusBadge tone={status.tone} busy={status.busy}>
                {status.label}
              </StatusBadge>
            )}
          </>
        }
        right={actions}
      />
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Datasets"
          className={cn(
            "bg-sidebar hidden w-64 shrink-0 border-r transition-[margin] duration-200 md:block",
            !sidebarOpen && "-ml-64",
          )}
          inert={!sidebarOpen}
        >
          {list}
        </aside>
        <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
          <SheetContent side="left" className="bg-sidebar w-72 p-0">
            <SheetHeader className="border-b">
              <SheetTitle>
                <Logo />
              </SheetTitle>
            </SheetHeader>
            {list}
          </SheetContent>
        </Sheet>
        <main className="min-h-0 min-w-0 flex-1">
          {selection.kind === "upload" ? (
            <UploadForm
              api={api}
              onUploaded={async (id) => {
                await refresh();
                setSelection({ kind: "dataset", id });
              }}
            />
          ) : (
            <DatasetView
              key={selection.id}
              api={api}
              datasetId={selection.id}
              onChange={onDatasetChange}
            />
          )}
        </main>
      </div>
    </div>
  );
}

function DatasetList({
  datasets,
  selection,
  error,
  onSelect,
}: {
  datasets: Dataset[];
  selection: Selection;
  error: string | null;
  onSelect: (next: Selection) => void;
}) {
  return (
    <nav className="flex flex-col gap-1 p-3">
      <Button
        variant={selection.kind === "upload" ? "secondary" : "outline"}
        className="mb-3 justify-start"
        onClick={() => onSelect({ kind: "upload" })}
      >
        <Plus />
        New dataset
      </Button>
      <p className="text-muted-foreground px-2 pb-1 text-xs font-medium">Datasets</p>
      {datasets.length === 0 && !error && (
        <p className="text-muted-foreground px-2 text-sm">No datasets yet.</p>
      )}
      {datasets.map((dataset) => {
        const active = selection.kind === "dataset" && selection.id === dataset.id;
        const status = datasetStatus(dataset.status);
        return (
          <button
            type="button"
            key={dataset.id}
            aria-current={active ? "page" : undefined}
            onClick={() => onSelect({ kind: "dataset", id: dataset.id })}
            className={cn(
              "focus-visible:ring-ring/50 flex h-9 items-center gap-2 rounded-md border border-transparent px-2 text-left text-sm outline-none transition-colors focus-visible:ring-[3px]",
              active
                ? "bg-sidebar-accent border-border font-medium shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <span className="min-w-0 flex-1 truncate">{dataset.name}</span>
            <StatusBadge tone={status.tone} busy={status.busy}>
              {status.label}
            </StatusBadge>
          </button>
        );
      })}
      {error && (
        <p role="alert" className="text-danger px-2 text-sm">
          {error}
        </p>
      )}
    </nav>
  );
}
