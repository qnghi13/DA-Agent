import { describe, expect, it } from "vitest";
import { SseParser, streamEvents } from "./sse";

describe("SseParser", () => {
  it("parses id, event, and data", () => {
    const parser = new SseParser();
    expect(parser.push('id: 7\nevent: tool\ndata: {"ok":true}\n\n')).toEqual([
      { id: "7", event: "tool", data: '{"ok":true}' },
    ]);
  });

  it("joins events split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push("id: 1\nevent: pl")).toEqual([]);
    expect(parser.push('an\ndata: {"steps"')).toEqual([]);
    expect(parser.push(':[]}\n\nid: 2\n')).toEqual([
      { id: "1", event: "plan", data: '{"steps":[]}' },
    ]);
  });

  it("ignores keep-alive comments and accepts CRLF", () => {
    const parser = new SseParser();
    expect(parser.push(": keep-alive\r\n\r\nevent: end\r\ndata: {}\r\n\r\n")).toEqual([
      { id: undefined, event: "end", data: "{}" },
    ]);
  });

  it("joins multi-line data with newlines and defaults the event name", () => {
    const parser = new SseParser();
    expect(parser.push("data: a\ndata: b\n\n")).toEqual([
      { id: undefined, event: "message", data: "a\nb" },
    ]);
  });
});

function streamOf(text: string): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("streamEvents", () => {
  it("reconnects with Last-Event-ID until the end event", async () => {
    const sent: (string | null)[] = [];
    const bodies = [
      'id: 1\nevent: plan\ndata: {"steps":[]}\n\n',
      'id: 2\nevent: done\ndata: {}\n\nevent: end\ndata: {"status":"succeeded"}\n\n',
    ];
    const fetcher = async (_url: string, init: RequestInit) => {
      sent.push(new Headers(init.headers).get("Last-Event-ID"));
      return streamOf(bodies.shift() ?? "");
    };
    const events: string[] = [];
    await streamEvents("/v1/runs/x/events", {
      headers: { Authorization: "Bearer k" },
      fetcher,
      retryMs: 0,
      onEvent: (event) => events.push(event.event),
    });
    expect(events).toEqual(["plan", "done", "end"]);
    expect(sent).toEqual([null, "1"]);
  });

  it("stops when aborted", async () => {
    const controller = new AbortController();
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      controller.abort();
      return streamOf('id: 1\nevent: plan\ndata: {}\n\n');
    };
    await streamEvents("/v1/runs/x/events", {
      fetcher,
      retryMs: 0,
      signal: controller.signal,
      onEvent: () => {},
    });
    expect(calls).toBe(1);
  });

  it("throws on an HTTP error instead of retrying", async () => {
    const fetcher = async () => new Response('{"detail":"not found"}', { status: 404 });
    await expect(
      streamEvents("/v1/runs/x/events", { fetcher, retryMs: 0, onEvent: () => {} }),
    ).rejects.toThrow("not found");
  });
});
