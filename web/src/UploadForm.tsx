import { type FormEvent, useState } from "react";
import { type Api, UPLOAD_ACCEPT, errorMessage } from "./api";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
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

  return (
    <form className="stack" onSubmit={submit}>
      <h2>New dataset</h2>
      <p className="muted">
        Upload CSV, Excel, or SQLite files, plus optional Markdown or text documents that explain
        them. The agent proposes how to read them; you review that before asking questions.
      </p>
      <label>
        Name
        <input
          required
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        Description <span className="muted">(what the data is, business rules, caveats)</span>
        <textarea
          rows={5}
          maxLength={20000}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label>
        Files
        <input
          type="file"
          multiple
          required
          accept={UPLOAD_ACCEPT}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
      </label>
      {files.length > 0 && (
        <ul className="file-list">
          {files.map((file) => (
            <li key={file.name}>
              {file.name} <span className="muted">{size(file.size)}</span>
            </li>
          ))}
        </ul>
      )}
      <div>
        <button type="submit" className="primary" disabled={busy || !name.trim() || !files.length}>
          {busy ? "Uploading…" : "Upload"}
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}
