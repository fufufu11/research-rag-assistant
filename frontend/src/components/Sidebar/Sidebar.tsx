import { useState } from "react";
import type { ApiClient } from "../../api/client";
import { friendlyApiError } from "../../api/errors";
import type { DocumentStatus } from "../../api/types";
import { useDeleteDocument, useDocuments } from "../../hooks/useDocuments";

const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "等待处理",
  processing: "处理中",
  ready: "就绪",
  failed: "失败",
};

interface SidebarProps {
  client: ApiClient;
}

export function Sidebar({ client }: SidebarProps) {
  const [documentsCollapsed, setDocumentsCollapsed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const documentsQuery = useDocuments(client);
  const deleteDocument = useDeleteDocument(client);
  const documents = documentsQuery.data?.items ?? [];

  const handleDeleteDocument = (id: string) => {
    setActionError(null);
    deleteDocument.mutate(id, {
      onError: (error) => {
        setActionError(friendlyApiError(error, "删除文档"));
      },
    });
  };

  return (
    <aside className="sidebar" data-testid="sidebar">
      <div className="sidebar-header">
        <span className="logo-dot" aria-hidden="true" />
        <span>
          research<span className="accent">·</span>rag
        </span>
      </div>

      <button type="button" className="new-chat-btn" data-testid="new-chat-btn">
        <span className="star" aria-hidden="true">
          ✦
        </span>
        <span>新建对话</span>
      </button>

      <input
        type="text"
        className="search-input"
        placeholder="搜索会话…"
        aria-label="搜索会话"
        data-testid="search-input"
      />

      <nav className="nav-section" data-testid="nav-history">
        <button type="button" className="nav-section-title">
          <span>历史会话</span>
          <span className="arrow" aria-hidden="true">
            ▾
          </span>
        </button>
        <div className="nav-empty">暂无会话</div>
      </nav>

      <nav className="nav-section" data-testid="nav-documents">
        <button
          type="button"
          className="nav-section-title"
          aria-label="文档库"
          aria-controls="document-list"
          aria-expanded={!documentsCollapsed}
          onClick={() => setDocumentsCollapsed((collapsed) => !collapsed)}
        >
          <span>文档库</span>
          <span className="arrow" aria-hidden="true">
            {documentsCollapsed ? "▸" : "▾"}
          </span>
        </button>

        {!documentsCollapsed && (
          <div id="document-list">
            {actionError && (
              <p className="nav-error" role="alert">
                {actionError}
              </p>
            )}
            {documentsQuery.isPending && (
              <div className="nav-empty" role="status">
                正在加载文档…
              </div>
            )}
            {documentsQuery.isError && (
              <p className="nav-error" role="alert">
                {friendlyApiError(documentsQuery.error, "加载文档")}
              </p>
            )}
            {documentsQuery.isSuccess && documents.length === 0 && (
              <div className="nav-empty">暂无文档</div>
            )}
            {documents.length > 0 && (
              <ul className="document-list">
                {documents.map((document) => (
                  <li className="document-item" key={document.id}>
                    <div className="document-copy">
                      <span className="document-name" title={document.original_name}>
                        {document.original_name}
                      </span>
                      <span
                        className={`document-status status-${document.status}`}
                      >
                        {document.page_count} 页 ·{" "}
                        {DOCUMENT_STATUS_LABELS[document.status]}
                      </span>
                      {document.status === "failed" && document.error_message && (
                        <span className="document-error" title={document.error_message}>
                          {document.error_message}
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="document-delete"
                      aria-label={`删除文档 ${document.original_name}`}
                      title="删除文档"
                      disabled={
                        deleteDocument.isPending &&
                        deleteDocument.variables === document.id
                      }
                      onClick={() => handleDeleteDocument(document.id)}
                    >
                      <span aria-hidden="true">×</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </nav>

      <div className="sidebar-footer" data-testid="sidebar-footer">
        <button type="button" className="footer-btn" data-testid="footer-settings">
          <span className="icon" aria-hidden="true">
            ⚙
          </span>
          <span>设置</span>
        </button>
        <button type="button" className="footer-btn" data-testid="footer-help">
          <span className="icon" aria-hidden="true">
            ?
          </span>
          <span>帮助</span>
        </button>
      </div>
    </aside>
  );
}
