import { useEffect, useRef, useState } from "react";
import { ApiClient, ApiClientError } from "./api/client";
import type { DocumentList } from "./api/types";

// T1 阶段：最小骨架 App。
// 渲染 hello world 标题 + API 健康检查占位（实际调用 GET /api/v1/documents）。
// 后续 ticket 会替换为侧栏 + 主区布局（T2）+ 业务功能（T3-T7）。
export function App({ apiClient }: { apiClient?: ApiClient }) {
  // 稳定化 client 引用，避免每次渲染新建导致 useEffect 反复触发。
  const clientRef = useRef<ApiClient>(apiClient ?? new ApiClient());
  const client = clientRef.current;

  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "error">(
    "idle",
  );
  const [documentCount, setDocumentCount] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    client
      .listDocuments()
      .then((res: DocumentList) => {
        if (cancelled) return;
        setDocumentCount(res.items.length);
        setStatus("ok");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMessage(
          err instanceof ApiClientError ? err.detail : String(err),
        );
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  return (
    <div className="app-root">
      <h1>科研文献智能问答</h1>
      <section data-testid="api-health-placeholder">
        <h2>API 健康检查</h2>
        {status === "loading" && <p>检查中…</p>}
        {status === "ok" && (
          <p>
            后端连通，已有 <strong>{documentCount}</strong> 篇文档
          </p>
        )}
        {status === "error" && (
          <p>
            后端连通失败：<code>{errorMessage}</code>
          </p>
        )}
      </section>
    </div>
  );
}
