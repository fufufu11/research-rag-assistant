import type {
  ConversationCreate,
  ConversationList,
  ConversationRead,
  DocumentList,
  DocumentRead,
  QueryRequest,
} from "./types";
import { parseSseStream, SseProtocolError, type SseHandlers } from "./sse";

// ApiClient：封装后端 REST API 调用。
// 设计取舍（ADR 0005）：
// - 用 fetch + ReadableStream，不引入 axios，减少依赖
// - baseUrl 默认空字符串（dev 走 vite proxy，prod 同源托管）
// - API key 从 localStorage 读，未设置则不发 Authorization header
// - 非 2xx 抛 ApiClientError，含 status + detail 便于上层处理
//
// T1 范围：仅实现 listDocuments 健康检查。CRUD 方法在 T3-T7 各 ticket 引入时
// 再加，遵循 YAGNI（不提前声明 stub）。
export class ApiClientError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`API error ${status}`);
    this.name = "ApiClientError";
    this.status = status;
    this.detail = detail;
  }
}

async function throwApiClientError(response: Response): Promise<never> {
  let detail: unknown = response.statusText;
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      detail = body.detail ?? response.statusText;
    }
  } catch {
    // Non-JSON error responses use the HTTP status text.
  }
  throw new ApiClientError(response.status, detail);
}

const API_KEY_STORAGE_KEY = "rag_api_key";

export class ApiClient {
  readonly baseUrl: string;
  readonly apiKey: string | null;

  constructor(options?: { baseUrl?: string; apiKey?: string | null }) {
    this.baseUrl = options?.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? "";
    this.apiKey =
      options?.apiKey !== undefined
        ? options.apiKey
        : window.localStorage.getItem(API_KEY_STORAGE_KEY);
  }

  private buildHeaders(extra?: HeadersInit): Headers {
    const headers = new Headers({
      Accept: "application/json",
      ...(extra as HeadersInit | undefined),
    });
    if (this.apiKey) {
      headers.set("Authorization", `Bearer ${this.apiKey}`);
    }
    return headers;
  }

  private buildUrl(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private async fetchResponse(
    path: string,
    init: RequestInit,
  ): Promise<Response> {
    const response = await fetch(this.buildUrl(path), {
      ...init,
      headers: this.buildHeaders(init.headers),
    });
    if (!response.ok) {
      await throwApiClientError(response);
    }
    return response;
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await this.fetchResponse(path, init);
    return (await response.json()) as T;
  }

  // 健康检查：列出文档（GET /api/v1/documents）。
  // T1 阶段仅验证前后端连通性。
  async listDocuments(): Promise<DocumentList> {
    return this.request<DocumentList>("/api/v1/documents", { method: "GET" });
  }

  async uploadDocument(file: File): Promise<DocumentRead> {
    const formData = new FormData();
    formData.append("file", file);
    return this.request<DocumentRead>("/api/v1/documents", {
      method: "POST",
      body: formData,
    });
  }

  async deleteDocument(id: string): Promise<void> {
    await this.fetchResponse(`/api/v1/documents/${id}`, {
      method: "DELETE",
    });
  }

  async listConversations(signal?: AbortSignal): Promise<ConversationList> {
    return this.request<ConversationList>("/api/v1/conversations", {
      method: "GET",
      signal,
    });
  }

  async createConversation(
    payload: ConversationCreate,
  ): Promise<ConversationRead> {
    return this.request<ConversationRead>("/api/v1/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async getConversation(
    id: string,
    signal?: AbortSignal,
  ): Promise<ConversationRead> {
    return this.request<ConversationRead>(
      `/api/v1/conversations/${encodeURIComponent(id)}`,
      { method: "GET", signal },
    );
  }

  async deleteConversation(id: string): Promise<void> {
    await this.fetchResponse(
      `/api/v1/conversations/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
  }

  async askQuestionStream(
    payload: QueryRequest,
    handlers: SseHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.fetchResponse("/api/v1/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
    });
    if (!response.body) {
      throw new SseProtocolError("SSE response body is not readable");
    }
    await parseSseStream(response.body, handlers);
  }
}
