import { FileSpreadsheet, Loader2, UploadCloud, X } from "lucide-react";
import { type DragEvent, type FormEvent, useRef, useState } from "react";
import { type Api, UPLOAD_ACCEPT, UPLOAD_LIMITS, errorMessage } from "./api";
import { SetupPage } from "./components/setup/stepper";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";
import { cn } from "./lib/utils";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  const mb = bytes / 1024 / 1024;
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`;
}

const ACCEPTED = UPLOAD_ACCEPT.split(",");

/** Files the API would refuse by type; it checks again, this only saves a round trip. */
function accepted(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED.some((suffix) => name.endsWith(suffix));
}

export function UploadForm({
  api,
  onUploaded,
}: {
  api: Api;
  onUploaded: (id: string) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    const incoming = Array.from(list ?? []);
    const refused = incoming.filter((file) => !accepted(file)).map((file) => file.name);
    setError(refused.length ? `Not a supported file type: ${refused.join(", ")}` : null);
    setFiles((current) => {
      const byName = new Map(current.map((file) => [file.name, file]));
      for (const file of incoming.filter(accepted)) byName.set(file.name, file);
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
      const { id } = await api.uploadDataset(name.trim(), description, files);
      await onUploaded(id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const tooMany = files.length > UPLOAD_LIMITS.files;
  const tooBig = files.filter((file) => file.size > UPLOAD_LIMITS.bytesPerFile);
  return (
    <SetupPage
      step={1}
      title="New dataset"
      description="Upload CSV, Excel, or SQLite files, plus optional Markdown or text documents that explain them. The agent proposes how to read them; you review that before asking questions."
    >
      <form onSubmit={submit} className="bg-card flex flex-col gap-5 rounded-xl border p-5 sm:p-6">
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-name">Name</Label>
          <Input
            id="dataset-name"
            required
            maxLength={200}
            placeholder="Sales 2026"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-description">
            Description <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Textarea
            id="dataset-description"
            rows={4}
            maxLength={20000}
            placeholder="What the data is, business rules, caveats. For example: revenue counts only completed orders."
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-files">Files</Label>
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
            className={cn(
              "flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
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
            <p className="text-muted-foreground text-xs">
              CSV, TSV, Excel, SQLite, Markdown, or text · up to {UPLOAD_LIMITS.files} files,{" "}
              {size(UPLOAD_LIMITS.bytesPerFile)} each
            </p>
            <input
              ref={picker}
              id="dataset-files"
              type="file"
              multiple
              accept={UPLOAD_ACCEPT}
              className="sr-only"
              onChange={(event) => {
                add(event.target.files);
                event.target.value = "";
              }}
            />
          </div>
          {files.length > 0 && (
            <ul className="divide-y rounded-lg border">
              {files.map((file) => (
                <li key={file.name} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <FileSpreadsheet className="text-muted-foreground size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span
                    className={cn(
                      "font-mono text-xs tabular-nums",
                      file.size > UPLOAD_LIMITS.bytesPerFile ? "text-danger" : "text-muted-foreground",
                    )}
                  >
                    {size(file.size)}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => setFiles((current) => current.filter((f) => f !== file))}
                  >
                    <X />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {(tooMany || tooBig.length > 0) && (
            <p className="text-danger text-sm">
              {tooMany
                ? `At most ${UPLOAD_LIMITS.files} files.`
                : `Too large: ${tooBig.map((f) => f.name).join(", ")}.`}
            </p>
          )}
        </div>
        {error && (
          <p role="alert" className="text-danger text-sm">
            {error}
          </p>
        )}
        <div className="flex items-center justify-end gap-3 border-t pt-5">
          <Button
            type="submit"
            disabled={busy || !name.trim() || !files.length || tooMany || tooBig.length > 0}
          >
            {busy && <Loader2 className="animate-spin" />}
            {busy ? "Uploading…" : "Upload and continue"}
          </Button>
        </div>
      </form>
    </SetupPage>
  );
}
