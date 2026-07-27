import { describe, expect, it, vi } from "vitest";
import { parseSseStream, SseEventError, SseProtocolError } from "./sse";

function byteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  });
}

describe("parseSseStream", () => {
  it("delivers token and done events across UTF-8 byte chunks and CRLF frames", async () => {
    const source = [
      'event: token\r\ndata: {"text":"你好"}\r\n\r\n',
      'event: done\r\ndata: {"citations":[],"request_id":"req-1","elapsed_ms":12,"conversation_id":"conv-1","message_id":"msg-1"}\r\n\r\n',
    ].join("");
    const bytes = new TextEncoder().encode(source);
    const chineseByte = new TextEncoder().encode(
      source.slice(0, source.indexOf("你")),
    ).length;
    const onToken = vi.fn();
    const onDone = vi.fn();

    await parseSseStream(
      byteStream([
        bytes.slice(0, 9),
        bytes.slice(9, chineseByte + 1),
        bytes.slice(chineseByte + 1, chineseByte + 2),
        bytes.slice(chineseByte + 2),
      ]),
      { onToken, onDone },
    );

    expect(onToken).toHaveBeenCalledWith("你好");
    expect(onDone).toHaveBeenCalledWith({
      citations: [],
      request_id: "req-1",
      elapsed_ms: 12,
      conversation_id: "conv-1",
      message_id: "msg-1",
    });
  });

  it("rejects a stream that closes before a done event", async () => {
    const stream = byteStream([
      new TextEncoder().encode('event: token\ndata: {"text":"partial"}'),
    ]);

    await expect(
      parseSseStream(stream, { onToken: vi.fn(), onDone: vi.fn() }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("reports and rejects a server error event", async () => {
    const onError = vi.fn();
    const stream = byteStream([
      new TextEncoder().encode(
        'event: error\ndata: {"detail":"provider secret"}\n\n',
      ),
    ]);

    await expect(
      parseSseStream(stream, {
        onToken: vi.fn(),
        onDone: vi.fn(),
        onError,
      }),
    ).rejects.toBeInstanceOf(SseEventError);
    expect(onError).toHaveBeenCalledWith("provider secret");
  });

  it("rejects malformed event data as a protocol error", async () => {
    const stream = byteStream([
      new TextEncoder().encode("event: token\ndata: not-json\n\n"),
    ]);

    await expect(
      parseSseStream(stream, { onToken: vi.fn(), onDone: vi.fn() }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("recognizes CRLF frame separators split between chunks", async () => {
    const source =
      'event: done\r\ndata: {"citations":[],"request_id":"req-2","elapsed_ms":1,"conversation_id":"conv-1","message_id":"msg-2"}\r\n\r\n';
    const bytes = new TextEncoder().encode(source);
    const split = source.indexOf("\r\n\r\n") + 1;
    const onDone = vi.fn();

    await parseSseStream(byteStream([bytes.slice(0, split), bytes.slice(split)]), {
      onToken: vi.fn(),
      onDone,
    });

    expect(onDone).toHaveBeenCalledOnce();
  });

  it("recognizes CRLF line endings split between chunks", async () => {
    const source =
      'event: token\r\ndata: {"text":"answer"}\r\n\r\n' +
      'event: done\r\ndata: {"citations":[],"request_id":"req-3","elapsed_ms":1,"conversation_id":"conv-1","message_id":"msg-3"}\r\n\r\n';
    const bytes = new TextEncoder().encode(source);
    const split = source.indexOf("\r\n") + 1;
    const onToken = vi.fn();

    await parseSseStream(byteStream([bytes.slice(0, split), bytes.slice(split)]), {
      onToken,
      onDone: vi.fn(),
    });

    expect(onToken).toHaveBeenCalledWith("answer");
  });

  it("rejects token events without text", async () => {
    const stream = byteStream([
      new TextEncoder().encode(
        'event: token\ndata: {"detail":"wrong shape"}\n\n' +
          'event: done\ndata: {"citations":[],"request_id":"req-3","elapsed_ms":1,"conversation_id":"conv-1","message_id":"msg-3"}\n\n',
      ),
    ]);

    await expect(
      parseSseStream(stream, { onToken: vi.fn(), onDone: vi.fn() }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("rejects incomplete done metadata", async () => {
    const stream = byteStream([
      new TextEncoder().encode(
        'event: done\ndata: {"citations":"wrong","request_id":"req-4"}\n\n',
      ),
    ]);

    await expect(
      parseSseStream(stream, { onToken: vi.fn(), onDone: vi.fn() }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("accepts the current backend done event without a message id", async () => {
    const onDone = vi.fn();
    const stream = byteStream([
      new TextEncoder().encode(
        'event: done\ndata: {"citations":[],"request_id":"req-5","elapsed_ms":2,"conversation_id":"conv-1"}\n\n',
      ),
    ]);

    await parseSseStream(stream, { onToken: vi.fn(), onDone });

    expect(onDone).toHaveBeenCalledWith({
      citations: [],
      request_id: "req-5",
      elapsed_ms: 2,
      conversation_id: "conv-1",
    });
  });

  it("rejects malformed citation metadata", async () => {
    const stream = byteStream([
      new TextEncoder().encode(
        'event: done\ndata: {"citations":[null],"request_id":"req-6","elapsed_ms":2,"conversation_id":"conv-1"}\n\n',
      ),
    ]);

    await expect(
      parseSseStream(stream, { onToken: vi.fn(), onDone: vi.fn() }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });
});
