import { describe, expect, it, vi } from "vitest";
import { parseSseStream } from "./sse";

function streamFromByteChunks(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

describe("parseSseStream", () => {
  it("解析跨数据块的 token、done 和 error 事件", async () => {
    const encoder = new TextEncoder();
    const body = [
      'event: token\r\ndata: {"text":"你好"}\r\n\r\n',
      'event: done\r\ndata: {"citations":[],"request_id":"r1","elapsed_ms":10,"conversation_id":"c1","message_id":"m1"}\r\n\r\n',
      'event: error\r\ndata: {"detail":"生成失败"}\r\n\r\n',
    ].join("");
    const bytes = encoder.encode(body);
    const splitInsideChineseCharacter = body.indexOf("你") + 1;
    const firstChunkLength = encoder.encode(
      body.slice(0, splitInsideChineseCharacter),
    ).length - 1;
    const chunks = [
      bytes.slice(0, 7),
      bytes.slice(7, firstChunkLength),
      bytes.slice(firstChunkLength, firstChunkLength + 1),
      bytes.slice(firstChunkLength + 1, 83),
      bytes.slice(83),
    ];
    const onToken = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();

    await parseSseStream(streamFromByteChunks(chunks), {
      onToken,
      onDone,
      onError,
    });

    expect(onToken).toHaveBeenCalledWith("你好");
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ request_id: "r1", message_id: "m1" }),
    );
    expect(onError).toHaveBeenCalledWith("生成失败");
  });

  it("连接关闭时处理没有尾随空行的缓冲事件", async () => {
    const bytes = new TextEncoder().encode(
      'event: token\ndata: {"text":"最后一段"}',
    );
    const onToken = vi.fn();

    await parseSseStream(streamFromByteChunks([bytes]), {
      onToken,
      onDone: vi.fn(),
      onError: vi.fn(),
    });

    expect(onToken).toHaveBeenCalledOnce();
    expect(onToken).toHaveBeenCalledWith("最后一段");
  });
});
