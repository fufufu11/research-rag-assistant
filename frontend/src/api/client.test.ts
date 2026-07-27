import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { ApiClient, ApiClientError } from "./client";
import type { ConversationList, DocumentList } from "./types";

// T1 阶段：ApiClient 健康检查测试。
// 验证：基础 GET 请求、Authorization header 注入、错误处理。

type FetchSpy = MockInstance<typeof globalThis.fetch>;

function mockFetchOk(body: unknown, status = 200): FetchSpy {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("ApiClient", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_API_BASE_URL", "");
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("构造时使用默认 base url（空字符串，走 vite proxy）", () => {
    const client = new ApiClient();
    expect(client.baseUrl).toBe("");
  });

  it("构造时从 localStorage 读取 API key", () => {
    window.localStorage.setItem("rag_api_key", "test-key-123");
    const client = new ApiClient();
    expect(client.apiKey).toBe("test-key-123");
  });

  it("listDocuments 调用 GET /api/v1/documents 并返回 DocumentList", async () => {
    const client = new ApiClient();
    const fakeResponse: DocumentList = { items: [] };
    const fetchSpy = mockFetchOk(fakeResponse);

    const result = await client.listDocuments();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/documents");
    expect(init?.method).toBe("GET");
    const headers = new Headers(init?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(result).toEqual(fakeResponse);
  });

  it("有 API key 时请求头注入 Authorization Bearer", async () => {
    window.localStorage.setItem("rag_api_key", "secret-key");
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });

    await client.listDocuments();

    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.method).toBe("GET");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer secret-key");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("无 API key 时不发送 Authorization header", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });

    await client.listDocuments();

    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.method).toBe("GET");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("HTTP 非 2xx 抛 ApiClientError 含状态码与 detail", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "未授权" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(client.listDocuments()).rejects.toMatchObject({
      name: "ApiClientError",
      status: 401,
      detail: "未授权",
    });
    expect(ApiClientError).toBeDefined();
  });

  it("HTTP 422 保留结构化 detail", async () => {
    const client = new ApiClient();
    const detail = [
      { loc: ["body", "document_ids"], msg: "invalid value" },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(client.listConversations()).rejects.toMatchObject({
      name: "ApiClientError",
      status: 422,
      detail,
    });
  });

  it("VITE_API_BASE_URL 设置时拼到请求 URL 前面", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });

    await client.listDocuments();

    expect(fetchSpy.mock.calls[0][0]).toBe(
      "http://localhost:8000/api/v1/documents",
    );
  });

  it("uploadDocument 使用 FormData 上传单个 PDF", async () => {
    const document = {
      id: "doc-1",
      original_name: "paper.pdf",
      stored_name: "doc-1.pdf",
      sha256: "abc123",
      page_count: 3,
      status: "pending" as const,
      error_message: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
    };
    const fetchSpy = mockFetchOk(document, 201);
    const client = new ApiClient();
    const file = new File(["pdf"], "paper.pdf", {
      type: "application/pdf",
    });

    await expect(client.uploadDocument(file)).resolves.toEqual(document);

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/documents");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBe(file);
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("deleteDocument 调用文档删除接口并接受 204 空响应", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = new ApiClient();

    await expect(client.deleteDocument("doc-1")).resolves.toBeUndefined();

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/documents/doc-1");
    expect(init?.method).toBe("DELETE");
  });

  it("listConversations 调用 GET /api/v1/conversations", async () => {
    const fakeResponse: ConversationList = { items: [] };
    const fetchSpy = mockFetchOk(fakeResponse);
    const client = new ApiClient();
    const controller = new AbortController();

    await expect(client.listConversations(controller.signal)).resolves.toEqual(
      fakeResponse,
    );

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/conversations");
    expect(init?.method).toBe("GET");
    expect(init?.signal).toBe(controller.signal);
  });

  it("createConversation 用 JSON POST 锁定选中的文档范围", async () => {
    const conversation = {
      id: "conversation-1",
      title: null,
      document_ids: ["doc-ready"],
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const fetchSpy = mockFetchOk(conversation, 201);
    const client = new ApiClient();

    await expect(
      client.createConversation({ document_ids: ["doc-ready"] }),
    ).resolves.toEqual(conversation);

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/conversations");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(JSON.stringify({ document_ids: ["doc-ready"] }));
  });

  it("getConversation 获取包含历史消息的会话详情", async () => {
    const conversation = {
      id: "conversation/1",
      title: "已有讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:01:00Z",
      messages: [],
    };
    const fetchSpy = mockFetchOk(conversation);
    const client = new ApiClient();
    const controller = new AbortController();

    await expect(
      client.getConversation("conversation/1", controller.signal),
    ).resolves.toEqual(conversation);

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/conversations/conversation%2F1");
    expect(init?.method).toBe("GET");
    expect(init?.signal).toBe(controller.signal);
  });

  it("deleteConversation 调用会话删除接口并接受 204 空响应", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = new ApiClient();

    await expect(
      client.deleteConversation("conversation/1"),
    ).resolves.toBeUndefined();

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/conversations/conversation%2F1");
    expect(init?.method).toBe("DELETE");
  });

  it("askQuestionStream posts an authenticated conversation request and delivers events", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: token\ndata: {"text":"answer"}\n\n' +
              'event: done\ndata: {"citations":[],"request_id":"req-1","elapsed_ms":5,"conversation_id":"conv-1","message_id":"msg-1"}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    const client = new ApiClient({ apiKey: "test-key" });
    const controller = new AbortController();
    const onToken = vi.fn();
    const onDone = vi.fn();

    await client.askQuestionStream(
      { question: "What changed?", conversation_id: "conv-1" },
      { onToken, onDone },
      controller.signal,
    );

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/queries");
    expect(init?.method).toBe("POST");
    expect(init?.signal).toBe(controller.signal);
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer test-key",
    );
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(JSON.parse(String(init?.body))).toEqual({
      question: "What changed?",
      conversation_id: "conv-1",
      stream: true,
    });
    expect(onToken).toHaveBeenCalledWith("answer");
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ request_id: "req-1", message_id: "msg-1" }),
    );
  });

  it.each([
    [400, "只支持 PDF", "upload"],
    [404, "文档不存在", "delete"],
    [500, "存储服务不可用", "list"],
  ] as const)(
    "文档接口 %i 错误抛出包含状态码与 detail 的结构化错误",
    async (status, detail, operation) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ detail }), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );
      const client = new ApiClient();

      const action =
        operation === "upload"
          ? client.uploadDocument(
              new File(["pdf"], "paper.pdf", { type: "application/pdf" }),
            )
          : operation === "delete"
            ? client.deleteDocument("missing")
            : client.listDocuments();

      await expect(action).rejects.toMatchObject({
        name: "ApiClientError",
        status,
        detail,
      });
    },
  );
});
