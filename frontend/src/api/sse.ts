import type { CitationRead } from "./types";

export interface StreamDoneData {
  citations: CitationRead[];
  request_id: string;
  elapsed_ms: number;
  conversation_id: string | null;
  message_id: string | null;
}

export interface SseHandlers {
  onToken: (token: string) => void;
  onDone: (data: StreamDoneData) => void;
  onError: (message: string) => void;
}

function readStringField(
  value: unknown,
  primary: string,
  fallback?: string,
): string | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as Record<string, unknown>;
  const candidate = record[primary] ?? (fallback ? record[fallback] : undefined);
  return typeof candidate === "string" ? candidate : undefined;
}

function dispatchEvent(block: string, handlers: SseHandlers): void {
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) return;
  const dataText = dataLines.join("\n");
  let data: unknown;
  try {
    data = JSON.parse(dataText) as unknown;
  } catch {
    data = dataText;
  }

  if (eventType === "token") {
    const token =
      typeof data === "string"
        ? data
        : readStringField(data, "text", "token") ?? "";
    handlers.onToken(token);
  } else if (eventType === "done" && typeof data === "object" && data !== null) {
    handlers.onDone(data as StreamDoneData);
  } else if (eventType === "error") {
    handlers.onError(
      readStringField(data, "detail", "message") ??
        (typeof data === "string" ? data : "未知错误"),
    );
  }
}

function drainCompleteEvents(
  buffer: string,
  handlers: SseHandlers,
): string {
  const separator = /\r?\n\r?\n/g;
  let consumedThrough = 0;
  let match: RegExpExecArray | null;

  while ((match = separator.exec(buffer)) !== null) {
    dispatchEvent(buffer.slice(consumedThrough, match.index), handlers);
    consumedThrough = match.index + match[0].length;
  }

  return buffer.slice(consumedThrough);
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
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = drainCompleteEvents(buffer, handlers);
  }

  buffer += decoder.decode();
  buffer = drainCompleteEvents(buffer, handlers);
  if (buffer.trim()) dispatchEvent(buffer, handlers);
}
