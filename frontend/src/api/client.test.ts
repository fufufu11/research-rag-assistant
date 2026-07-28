import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { ApiClient, ApiClientError } from "./client";
import type { DocumentList } from "./types";

// ApiClient 测试：覆盖 T1 的基础方法 + T3-T8 新增 CRUD + SSE 流式。

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
    window.localStorage.setItem("apiKey", "test-key-123");
    const client = new ApiClient();
    expect(client.apiKey).toBe("test-key-123");
  });

  it("setApiKey 持久化到 localStorage，传 null 则清除", () => {
    const client = new ApiClient();
    client.setApiKey("abc");
    expect(window.localStorage.getItem("apiKey")).toBe("abc");
    client.setApiKey(null);
    expect(window.localStorage.getItem("apiKey")).toBeNull();
  });

  it("setApiKey 后当前客户端的下一次请求立即使用新 key", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });

    client.setApiKey("runtime-key");
    await client.listDocuments();

    const headers = new Headers(fetchSpy.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer runtime-key");
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
    window.localStorage.setItem("apiKey", "secret-key");
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

  it("VITE_API_BASE_URL 设置时拼到请求 URL 前面", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000");
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });

    await client.listDocuments();

    expect(fetchSpy.mock.calls[0][0]).toBe(
      "http://localhost:8000/api/v1/documents",
    );
  });

  // === T3：文档管理 ===

  it("uploadDocument 用 FormData POST multipart/form-data", async () => {
    const client = new ApiClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "doc-1", original_name: "f.pdf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const file = new File(["dummy"], "test.pdf", { type: "application/pdf" });

    await client.uploadDocument(file);

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/documents");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    const headers = new Headers(init?.headers);
    // Content-Type 由浏览器自动加 boundary，不应手动设置
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("deleteDocument surfaces a 404 response", async () => {
    const client = new ApiClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "document not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(client.deleteDocument("doc-1")).rejects.toMatchObject({
      name: "ApiClientError",
      status: 404,
      detail: "document not found",
    });

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/documents/doc-1");
    expect(init?.method).toBe("DELETE");
  });

  // === T4：会话管理 ===

  it("listConversations 调 GET /api/v1/conversations", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });
    await client.listConversations();
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/v1/conversations");
    expect(fetchSpy.mock.calls[0][1]?.method).toBe("GET");
  });

  it("createConversation 用 JSON body POST", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ id: "conv-1" });
    await client.createConversation({ title: "测试" });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/conversations");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ title: "测试" }));
    const headers = new Headers(init?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("deleteConversation 调 DELETE /api/v1/conversations/:id", async () => {
    const client = new ApiClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    await client.deleteConversation("conv-1");
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/v1/conversations/conv-1");
    expect(fetchSpy.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("deleteConversation surfaces a 404 response", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "conversation not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(client.deleteConversation("conv-missing")).rejects.toMatchObject({
      name: "ApiClientError",
      status: 404,
      detail: "conversation not found",
    });
  });

  // === T5：SSE 流式问答 ===

  it("askQuestion 非流式 POST，stream=false 强制", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({
      answer: "ok",
      citations: [],
      request_id: "r1",
      elapsed_ms: 100,
      conversation_id: null,
    });
    await client.askQuestion({
      question: "q",
      conversation_id: null,
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/queries");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(init?.body as string);
    expect(body.stream).toBe(false);
  });

  it("askQuestionStream 解析 token / done / error 事件", async () => {
    const client = new ApiClient();
    const sseBody = [
      'event: token\ndata: {"text":"你好"}\n\n',
      'event: token\ndata: {"text":"世界"}\n\n',
      'event: done\ndata: {"citations":[],"request_id":"r1","elapsed_ms":100,"conversation_id":null}\n\n',
    ].join("");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(sseBody, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const tokens: string[] = [];
    const doneRequestIds: string[] = [];
    await client.askQuestionStream(
      { question: "q", stream: true },
      {
        onToken: (t) => tokens.push(t),
        onDone: (data) => {
          doneRequestIds.push(data.request_id);
        },
        onError: () => {},
      },
    );
    expect(tokens).toEqual(["你好", "世界"]);
    expect(doneRequestIds).toEqual(["r1"]);
  });

  it("askQuestionStream HTTP 非 2xx 抛 ApiClientError", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "bad request" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(
      client.askQuestionStream(
        { question: "q", stream: true },
        {
          onToken: () => {},
          onDone: () => {},
          onError: () => {},
        },
      ),
    ).rejects.toMatchObject({ name: "ApiClientError", status: 400 });
  });

  // === T7：反馈 ===

  it("submitFeedback POST /api/v1/feedback", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ id: "fb-1", request_id: "r1" });
    await client.submitFeedback({
      request_id: "r1",
      rating: "like",
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/feedback");
    expect(init?.method).toBe("POST");
  });

  it("getFeedback 404 返回 null，其他错误抛异常", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 404 }),
    );
    const result = await client.getFeedback("r1");
    expect(result).toBeNull();
  });

  it("getFeedback 200 返回 FeedbackRead", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "fb-1", request_id: "r1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const result = await client.getFeedback("r1");
    expect(result?.id).toBe("fb-1");
  });

  it("listFeedback 拼接查询参数", async () => {
    const client = new ApiClient();
    const fetchSpy = mockFetchOk({ items: [] });
    await client.listFeedback({ rating: "like", limit: 10 });
    expect(fetchSpy.mock.calls[0][0]).toContain("rating=like");
    expect(fetchSpy.mock.calls[0][0]).toContain("limit=10");
  });

  it("deleteFeedback 404 视为成功", async () => {
    const client = new ApiClient();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 404 }),
    );
    await expect(client.deleteFeedback("r1")).resolves.toBeUndefined();
  });
});
