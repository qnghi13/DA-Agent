import { FileSpreadsheet, Loader2, UploadCloud, X } from "lucide-react";
import { type DragEvent, type FormEvent, useRef, useState } from "react";
import { type Api, UPLOAD_ACCEPT, UPLOAD_LIMITS } from "./api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import type { Messages } from "@/i18n/en";
import { SetupPage } from "./components/setup/stepper";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";
import { cn } from "./lib/utils";

function size(bytes: number, m: Messages): string {
  const number = new Intl.NumberFormat(m.locale, { maximumFractionDigits: 1 });
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${number.format(bytes / 1024)} KB`;
  return `${number.format(bytes / 1024 / 1024)} MB`;
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
  const m = useT();
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
    setError(refused.length ? m.upload.unsupported(refused.join(", ")) : null);
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
      setError(errorMessage(caught, m));
    } finally {
      setBusy(false);
    }
  }

  const tooMany = files.length > UPLOAD_LIMITS.files;
  const tooBig = files.filter((file) => file.size > UPLOAD_LIMITS.bytesPerFile);
  return (
    <SetupPage
      step={1}
      title={m.workspace.newDataset}
      description={m.upload.intro}
    >
      <form onSubmit={submit} className="bg-card flex flex-col gap-5 rounded-xl border p-5 sm:p-6">
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-name">{m.upload.name}</Label>
          <Input
            id="dataset-name"
            required
            maxLength={200}
            placeholder={m.upload.namePlaceholder}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-description">
            {m.upload.description}{" "}
            <span className="text-muted-foreground font-normal">{m.upload.optional}</span>
          </Label>
          <Textarea
            id="dataset-description"
            rows={4}
            maxLength={20000}
            placeholder={m.upload.descriptionPlaceholder}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="dataset-files">{m.upload.files}</Label>
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
              {m.upload.dragHere}{" "}
              <button
                type="button"
                className="font-medium underline underline-offset-4"
                onClick={() => picker.current?.click()}
              >
                {m.upload.choose}
              </button>
            </p>
            <p className="text-muted-foreground text-xs">
              {m.upload.limits(UPLOAD_LIMITS.files, size(UPLOAD_LIMITS.bytesPerFile, m))}
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
                    {size(file.size, m)}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={m.upload.remove(file.name)}
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
                ? m.upload.tooMany(UPLOAD_LIMITS.files)
                : m.upload.tooLarge(tooBig.map((f) => f.name).join(", "))}
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
            {busy ? m.upload.uploading : m.upload.submit}
          </Button>
        </div>
      </form>
    </SetupPage>
  );
}
