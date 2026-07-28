import type {
  ConversationCreate,
  ConversationList,
  ConversationRead,
  DocumentList,
  DocumentRead,
  FeedbackCreate,
  FeedbackList,
  FeedbackRead,
  QueryRequest,
  QueryResponse,
} from "./types";
import { parseSseStream } from "./sse";
import type { SseHandlers } from "./sse";

// ApiClient：封装后端 REST API 调用。
// 设计取舍（ADR 0005）：
// - 用 fetch + ReadableStream，不引入 axios，减少依赖
// - baseUrl 默认空字符串（dev 走 vite proxy，prod 同源托管）
// - API key 从 localStorage 读，未设置则不发 Authorization header
// - 非 2xx 抛 ApiClientError，含 status + detail 便于上层处理
export class ApiClientError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`API error ${status}: ${detail}`);
    this.name = "ApiClientError";
    this.status = status;
    this.detail = detail;
  }
}

async function throwApiClientError(response: Response): Promise<never> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // Non-JSON error responses use the HTTP status text.
  }
  throw new ApiClientError(response.status, detail);
}

const API_KEY_STORAGE_KEY = "apiKey";

export class ApiClient {
  readonly baseUrl: string;
  apiKey: string | null;

  constructor(options?: { baseUrl?: string; apiKey?: string | null }) {
    this.baseUrl = options?.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? "";
    this.apiKey =
      options?.apiKey !== undefined
        ? options.apiKey
        : window.localStorage.getItem(API_KEY_STORAGE_KEY);
  }

  /** 设置 API key（持久化到 localStorage） */
  setApiKey(key: string | null): void {
    this.apiKey = key;
    if (key) {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, key);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
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

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(this.buildUrl(path), {
      ...init,
      headers: this.buildHeaders(init.headers),
    });
    if (!response.ok) {
      await throwApiClientError(response);
    }
    return (await response.json()) as T;
  }

  // === 文档管理 ===

  async listDocuments(): Promise<DocumentList> {
    return this.request<DocumentList>("/api/v1/documents", { method: "GET" });
  }

  async getDocument(id: string): Promise<DocumentRead> {
    return this.request<DocumentRead>(`/api/v1/documents/${id}`, {
      method: "GET",
    });
  }

  async uploadDocument(file: File): Promise<DocumentRead> {
    const form = new FormData();
    form.append("file", file);
    // 注意：multipart/form-data 不要手动设 Content-Type，浏览器会自动加 boundary
    const headers = this.buildHeaders();
    headers.delete("Content-Type");
    const response = await fetch(this.buildUrl("/api/v1/documents"), {
      method: "POST",
      headers,
      body: form,
    });
    if (!response.ok) {
      await throwApiClientError(response);
    }
    return (await response.json()) as DocumentRead;
  }

  async deleteDocument(id: string): Promise<void> {
    const response = await fetch(this.buildUrl(`/api/v1/documents/${id}`), {
      method: "DELETE",
      headers: this.buildHeaders(),
    });
    if (!response.ok) {
      await throwApiClientError(response);
    }
  }

  // === 会话管理 ===

  async listConversations(): Promise<ConversationList> {
    return this.request<ConversationList>("/api/v1/conversations", {
      method: "GET",
    });
  }

  async createConversation(payload: ConversationCreate): Promise<ConversationRead> {
    return this.request<ConversationRead>("/api/v1/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async getConversation(id: string): Promise<ConversationRead> {
    return this.request<ConversationRead>(`/api/v1/conversations/${id}`, {
      method: "GET",
    });
  }

  async deleteConversation(id: string): Promise<void> {
    const response = await fetch(this.buildUrl(`/api/v1/conversations/${id}`), {
      method: "DELETE",
      headers: this.buildHeaders(),
    });
    if (!response.ok) {
      await throwApiClientError(response);
    }
  }

  // === 问答（SSE 流式 + 非流式） ===

  /** 非流式问答 */
  async askQuestion(payload: QueryRequest): Promise<QueryResponse> {
    return this.request<QueryResponse>("/api/v1/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, stream: false }),
    });
  }

  /**
   * 流式问答（SSE）。
   * 用 fetch + ReadableStream 解析，支持 POST + 自定义 headers（EventSource 不支持）。
   * 协议：text/event-stream，事件类型 token / done / error。
   * @param payload QueryRequest（stream=true 会被强制设置）
   * @param handlers 事件回调
   */
  async askQuestionStream(
    payload: QueryRequest,
    handlers: SseHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(this.buildUrl("/api/v1/queries"), {
      method: "POST",
      headers: this.buildHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
    });

    if (!response.ok) {
      await throwApiClientError(response);
    }

    if (!response.body) {
      throw new Error("Response body is not readable");
    }
    await parseSseStream(response.body, handlers);
  }

  // === 反馈 ===

  async submitFeedback(payload: FeedbackCreate): Promise<FeedbackRead> {
    return this.request<FeedbackRead>("/api/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async getFeedback(requestId: string): Promise<FeedbackRead | null> {
    const response = await fetch(
      this.buildUrl(`/api/v1/feedback/${encodeURIComponent(requestId)}`),
      {
        method: "GET",
        headers: this.buildHeaders(),
      },
    );
    if (response.status === 404) return null;
    if (!response.ok) {
      await throwApiClientError(response);
    }
    return (await response.json()) as FeedbackRead;
  }

  async listFeedback(params?: {
    rating?: string;
    conversation_id?: string;
    limit?: number;
  }): Promise<FeedbackList> {
    const search = new URLSearchParams();
    if (params?.rating) search.set("rating", params.rating);
    if (params?.conversation_id) search.set("conversation_id", params.conversation_id);
    if (params?.limit) search.set("limit", String(params.limit));
    const query = search.toString();
    const path = query ? `/api/v1/feedback?${query}` : "/api/v1/feedback";
    return this.request<FeedbackList>(path, { method: "GET" });
  }

  async deleteFeedback(requestId: string): Promise<void> {
    const response = await fetch(
      this.buildUrl(`/api/v1/feedback/${encodeURIComponent(requestId)}`),
      {
        method: "DELETE",
        headers: this.buildHeaders(),
      },
    );
    if (!response.ok && response.status !== 404) {
      await throwApiClientError(response);
    }
  }
}
