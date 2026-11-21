import { FileSpreadsheet, Loader2, UploadCloud, X } from "lucide-react";
import { type DragEvent, type FormEvent, useRef, useState } from "react";
import { type Api, type Dataset, UPLOAD_ACCEPT, UPLOAD_LIMITS, errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const ACCEPTED = UPLOAD_ACCEPT.split(",");
const accepted = (file: File) => ACCEPTED.some((suffix) => file.name.toLowerCase().endsWith(suffix));

/**
 * Adds files to a ready dataset as its next version. Each file is added, or replaces one of the
 * dataset's files (docs/design/2026-11-21-add-files.md). The check runs after the upload.
 */
export function AddFilesDialog({
  api,
  dataset,
  open,
  onClose,
  onUploaded,
}: {
  api: Api;
  dataset: Dataset;
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-lg">
        {open && <AddFilesForm api={api} dataset={dataset} onClose={onClose} onUploaded={onUploaded} />}
      </DialogContent>
    </Dialog>
  );
}

interface Pick {
  file: File;
  /** Stored name of the dataset file this one replaces; empty to add. */
  replaces: string;
}

function AddFilesForm({
  api,
  dataset,
  onClose,
  onUploaded,
}: {
  api: Api;
  dataset: Dataset;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [picks, setPicks] = useState<Pick[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    const incoming = Array.from(list ?? []);
    const refused = incoming.filter((file) => !accepted(file)).map((file) => file.name);
    setError(refused.length ? `Not a supported file type: ${refused.join(", ")}` : null);
    setPicks((current) => {
      const byName = new Map(current.map((pick) => [pick.file.name, pick]));
      for (const file of incoming.filter(accepted)) {
        byName.set(file.name, { file, replaces: byName.get(file.name)?.replaces ?? "" });
      }
      return [...byName.values()];
    });
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    add(event.dataTransfer.files);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.addFiles(
        dataset.id,
        picks.map((p) => p.file),
        picks.map((p) => p.replaces || null),
      );
      onUploaded();
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const tooBig = picks.filter((p) => p.file.size > UPLOAD_LIMITS.bytesPerFile);
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>Add files</DialogTitle>
        <DialogDescription>
          The new files become the next version of {dataset.name}. Earlier answers keep the data
          they were made from.
        </DialogDescription>
      </DialogHeader>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        className={cn(
          "flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-6 text-center transition-colors",
          dragging ? "border-foreground bg-accent" : "bg-muted/50",
        )}
      >
        <UploadCloud className="text-muted-foreground size-6" aria-hidden />
        <p className="text-sm">
          Drag files here, or{" "}
          <button
            type="button"
            className="font-medium underline underline-offset-4"
            onClick={() => picker.current?.click()}
          >
            choose files
          </button>
        </p>
        <input
          ref={picker}
          type="file"
          multiple
          accept={UPLOAD_ACCEPT}
          className="sr-only"
          aria-label="Choose files to add"
          onChange={(event) => {
            add(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      {picks.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {picks.map(({ file, replaces }) => (
            <li key={file.name} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <FileSpreadsheet className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{file.name}</span>
              <select
                aria-label={`What to do with ${file.name}`}
                value={replaces}
                onChange={(event) =>
                  setPicks((current) =>
                    current.map((p) => (p.file === file ? { ...p, replaces: event.target.value } : p)),
                  )
                }
                className="border-input bg-background h-8 max-w-48 rounded-md border px-2 text-sm"
              >
                <option value="">Add</option>
                {dataset.files.map((existing) => (
                  <option key={existing.stored_as} value={existing.stored_as}>
                    Replace {existing.name}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={`Remove ${file.name}`}
                onClick={() => setPicks((current) => current.filter((p) => p.file !== file))}
              >
                <X />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {tooBig.length > 0 && (
        <p className="text-danger text-sm">Too large: {tooBig.map((p) => p.file.name).join(", ")}.</p>
      )}
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || picks.length === 0 || tooBig.length > 0}>
          {busy && <Loader2 className="animate-spin" />}
          {busy ? "Uploading…" : "Add files"}
        </Button>
      </DialogFooter>
    </form>
  );
}
