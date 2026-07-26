import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { ApiClient, ApiClientError } from "./client";
import type { DocumentList } from "./types";

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
