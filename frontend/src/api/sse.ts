import type { CitationRead } from "./types";

export class SseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SseProtocolError";
  }
}

export class SseEventError extends Error {
  constructor(readonly detail: string) {
    super("The server ended the SSE stream with an error event");
    this.name = "SseEventError";
  }
}

export interface SseDoneEvent {
  citations: CitationRead[];
  request_id: string;
  elapsed_ms: number;
  conversation_id: string | null;
  message_id?: string | null;
}

export interface SseHandlers {
  onToken: (text: string) => void;
  onDone: (event: SseDoneEvent) => void;
  onError?: (detail: string) => void;
}

interface ParsedEvent {
  event: string;
  data: string;
}

function isCitation(value: unknown): value is CitationRead {
  if (typeof value !== "object" || value === null) return false;
  const citation = value as Record<string, unknown>;
  return (
    typeof citation.document_id === "string" &&
    typeof citation.document_name === "string" &&
    typeof citation.start_page === "number" &&
    typeof citation.end_page === "number" &&
    typeof citation.chunk_index === "number" &&
    typeof citation.snippet === "string" &&
    typeof citation.score === "number"
  );
}

function parseFrame(frame: string): ParsedEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trimStart();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }

  return data.length > 0 ? { event, data: data.join("\n") } : null;
}

function dispatchFrame(frame: string, handlers: SseHandlers): boolean {
  const parsed = parseFrame(frame);
  if (!parsed) return false;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(parsed.data) as Record<string, unknown>;
  } catch {
    throw new SseProtocolError("SSE event data is not valid JSON");
  }

  if (parsed.event === "token") {
    if (typeof payload.text !== "string") {
      throw new SseProtocolError("SSE token event is missing text");
    }
    handlers.onToken(payload.text);
    return false;
  }
  if (parsed.event === "done") {
    const nullableString = (value: unknown) =>
      value === null || typeof value === "string";
    if (
      !Array.isArray(payload.citations) ||
      !payload.citations.every(isCitation) ||
      typeof payload.request_id !== "string" ||
      typeof payload.elapsed_ms !== "number" ||
      !nullableString(payload.conversation_id) ||
      (payload.message_id !== undefined && !nullableString(payload.message_id))
    ) {
      throw new SseProtocolError("SSE done event has invalid metadata");
    }
    handlers.onDone(payload as unknown as SseDoneEvent);
    return true;
  }
  if (parsed.event === "error") {
    const detail = String(payload.detail ?? "");
    handlers.onError?.(detail);
    throw new SseEventError(detail);
  }
  return false;
}

export async function parseSseStream(
  stream: ReadableStream<Uint8Array>,
  handlers: SseHandlers,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");

    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      const frame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      if (dispatchFrame(frame, handlers)) return;
      separator = buffer.indexOf("\n\n");
    }

    if (done) {
      if (buffer.trim() && dispatchFrame(buffer, handlers)) return;
      throw new SseProtocolError("SSE stream ended before a terminal event");
    }
  }
}
