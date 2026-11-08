// Server-sent events read with fetch, because EventSource cannot send the bearer key.
// The API ends each connection after a few minutes or on errors; we reconnect with
// Last-Event-ID until the `end` event (docs/adr/0021-progress-events.md).

import { ApiError, errorDetail } from "./api";

export interface SseEvent {
  id: string | undefined;
  event: string;
  data: string;
}

/** Incremental parser for the text/event-stream format. */
export class SseParser {
  private buffer = "";
  private id: string | undefined;
  private event = "";
  private data: string[] = [];

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r\n|\r|\n/);
    // The last piece may be an unfinished line.
    this.buffer = lines.pop() ?? "";
    const out: SseEvent[] = [];
    for (const line of lines) {
      if (line === "") {
        if (this.data.length || this.event) {
          out.push({ id: this.id, event: this.event || "message", data: this.data.join("\n") });
        }
        this.id = undefined;
        this.event = "";
        this.data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "id") this.id = value;
      else if (field === "event") this.event = value;
      else if (field === "data") this.data.push(value);
    }
    return out;
  }
}

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

export interface StreamOptions {
  headers?: Record<string, string>;
  onEvent: (event: SseEvent) => void;
  signal?: AbortSignal;
  fetcher?: Fetcher;
  /** Wait before reconnecting. */
  retryMs?: number;
  /** Give up after this many connections in a row that fail or bring nothing. */
  maxIdleRetries?: number;
}

/**
 * Read a stream until its `end` event, reconnecting after dropped connections.
 * HTTP errors (401, 404, 429) are thrown, since retrying cannot fix them.
 */
export async function streamEvents(url: string, options: StreamOptions): Promise<void> {
  const fetcher = options.fetcher ?? ((u, init) => fetch(u, init));
  const retryMs = options.retryMs ?? 1000;
  const maxIdle = options.maxIdleRetries ?? 10;
  let lastId: string | undefined;
  let idle = 0;

  while (!options.signal?.aborted) {
    const headers = new Headers(options.headers);
    headers.set("Accept", "text/event-stream");
    if (lastId) headers.set("Last-Event-ID", lastId);
    let received = false;
    try {
      const response = await fetcher(url, { headers, signal: options.signal });
      if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
      if (!response.body) throw new Error("the event stream has no body");
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const event of parser.push(value)) {
          received = true;
          if (event.id) lastId = event.id;
          options.onEvent(event);
          if (event.event === "end") return;
        }
        if (options.signal?.aborted) return;
      }
    } catch (error) {
      if (error instanceof ApiError || options.signal?.aborted) {
        if (options.signal?.aborted) return;
        throw error;
      }
      // A network error: fall through and reconnect.
    }
    idle = received ? 0 : idle + 1;
    if (idle > maxIdle) throw new Error("lost the connection to the event stream");
    if (retryMs > 0) await new Promise((resolve) => setTimeout(resolve, retryMs));
  }
}
