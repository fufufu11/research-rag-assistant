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

  /** 设置 API key（持久化到 localStorage） */
  setApiKey(key: string | null): void {
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
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // 响应体非 JSON，用 statusText
      }
      throw new ApiClientError(response.status, detail);
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
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
    }
    return (await response.json()) as DocumentRead;
  }

  async deleteDocument(id: string): Promise<void> {
    const response = await fetch(this.buildUrl(`/api/v1/documents/${id}`), {
      method: "DELETE",
      headers: this.buildHeaders(),
    });
    if (!response.ok && response.status !== 404) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
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
    if (!response.ok && response.status !== 404) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
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
    handlers: {
      onToken: (token: string) => void;
      onDone: (data: {
        citations: import("./types").CitationRead[];
        request_id: string;
        elapsed_ms: number;
        conversation_id: string | null;
      }) => void;
      onError: (message: string) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch(this.buildUrl("/api/v1/queries"), {
      method: "POST",
      headers: this.buildHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
    });

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("Response body is not readable");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 事件以空行分隔，按事件块解析
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";

      for (const eventBlock of events) {
        const lines = eventBlock.split("\n");
        let eventType = "message";
        let dataStr = "";
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataStr += line.slice(5).trim();
          }
        }
        if (!dataStr) continue;

        try {
          const data = JSON.parse(dataStr);
          if (eventType === "token") {
            handlers.onToken(
              typeof data === "string" ? data : (data.text ?? data.token ?? ""),
            );
          } else if (eventType === "done") {
            handlers.onDone(data);
          } else if (eventType === "error") {
            handlers.onError(data.detail ?? data.message ?? "未知错误");
          }
        } catch {
          // 非 JSON data（可能是纯 token 字符串）
          if (eventType === "token") {
            handlers.onToken(dataStr);
          }
        }
      }
    }
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
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
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
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // ignore
      }
      throw new ApiClientError(response.status, detail);
    }
  }
}
