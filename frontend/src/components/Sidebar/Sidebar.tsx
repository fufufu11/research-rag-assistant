import { useState } from "react";
import { friendlyApiError } from "../../api/errors";
import { useApp } from "../../store/AppContext";
import {
  useConversations,
  useCreateConversation,
  useDeleteConversation,
} from "../../hooks/useConversations";
import {
  useDeleteDocument,
  useDocuments,
} from "../../hooks/useDocuments";
import type { ActiveView } from "../../store/AppContext";

const DOCUMENT_STATUS_LABELS = {
  pending: "等待处理",
  processing: "处理中",
  ready: "就绪",
  failed: "失败",
} as const;

function formatConversationDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN").format(new Date(value));
}

// Sidebar：左侧栏组件（260px 深棕背景）
// T3-T4：接入真实文档与会话列表（TanStack Query 缓存 + 失效）
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function Sidebar() {
  const {
    client,
    activeView,
    setActiveView,
    isMobileNavOpen,
    setIsMobileNavOpen,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  } = useApp();
  const [searchKeyword, setSearchKeyword] = useState("");
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [documentsCollapsed, setDocumentsCollapsed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const conversationsQuery = useConversations(client);
  const documentsQuery = useDocuments(client);
  const deleteConversation = useDeleteConversation(client);
  const deleteDocument = useDeleteDocument(client);
  const createConversation = useCreateConversation(client);

  const conversations = conversationsQuery.data?.items ?? [];
  const documents = documentsQuery.data?.items ?? [];

  const filteredConversations = searchKeyword
    ? conversations.filter((c) =>
        (c.title ?? "未命名会话").includes(searchKeyword),
      )
    : conversations;

  const handleNewChat = () => {
    setActionError(null);
    setActiveView("chat");
    setIsMobileNavOpen(false);
    createConversation.mutate(
      {
        document_ids: currentDocumentIds.length ? currentDocumentIds : null,
      },
      {
        onSuccess: (conversation) => {
          setCurrentConversationId(conversation.id);
          setCurrentDocumentIds(conversation.document_ids ?? []);
        },
        onError: (error) => {
          setActionError(friendlyApiError(error, "创建会话"));
        },
      },
    );
  };

  const handleSelectConversation = (id: string, docIds: string[] | null) => {
    setCurrentConversationId(id);
    setCurrentDocumentIds(docIds ?? []);
    setActiveView("chat");
    setIsMobileNavOpen(false);
  };

  const handleDeleteConversation = (id: string) => {
    setActionError(null);
    deleteConversation.mutate(id, {
      onSuccess: () => {
        if (currentConversationId === id) {
          setCurrentConversationId(null);
          setCurrentDocumentIds([]);
        }
      },
      onError: (error) => {
        setActionError(friendlyApiError(error, "删除会话"));
      },
    });
  };

  const handleDeleteDocument = (id: string) => {
    setActionError(null);
    deleteDocument.mutate(id, {
      onSuccess: () => {
        if (currentDocumentIds.includes(id)) {
          setCurrentConversationId(null);
          setCurrentDocumentIds(
            currentDocumentIds.filter((documentId) => documentId !== id),
          );
        }
      },
      onError: (error) => {
        setActionError(friendlyApiError(error, "删除文档"));
      },
    });
  };

  const handleToggleDocument = (id: string) => {
    const nextDocumentIds = currentDocumentIds.includes(id)
      ? currentDocumentIds.filter((documentId) => documentId !== id)
      : [...currentDocumentIds, id];

    // 文档范围属于会话快照。改变范围后开启新对话，避免后端继续使用旧会话范围。
    setCurrentConversationId(null);
    setCurrentDocumentIds(nextDocumentIds);
    setActiveView("chat");
    setIsMobileNavOpen(false);
  };

  const handleFooterClick = (view: ActiveView) => {
    setActiveView(view);
    setIsMobileNavOpen(false);
  };

  return (
    <aside
      id="app-sidebar"
      className={`sidebar ${isMobileNavOpen ? "mobile-open" : ""}`}
      data-testid="sidebar"
    >
      <div className="sidebar-header">
        <span className="logo-dot" aria-hidden="true" />
        <span>
          research<span className="accent">·</span>rag
        </span>
        <button
          type="button"
          className="sidebar-close-button"
          aria-label="关闭导航"
          title="关闭导航"
          onClick={() => setIsMobileNavOpen(false)}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>

      <button
        type="button"
        className="new-chat-btn"
        data-testid="new-chat-btn"
        onClick={handleNewChat}
        disabled={createConversation.isPending}
      >
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
        value={searchKeyword}
        onChange={(e) => setSearchKeyword(e.target.value)}
        data-testid="search-input"
      />

      {actionError && (
        <div className="nav-error" role="alert">
          {actionError}
        </div>
      )}

      {/* 历史会话 */}
      <nav className="nav-section" data-testid="nav-history">
        <button
          type="button"
          className="nav-section-title"
          onClick={() => setHistoryCollapsed((v) => !v)}
        >
          <span>历史会话</span>
          <span className="arrow" aria-hidden="true">
            {historyCollapsed ? "▸" : "▾"}
          </span>
        </button>
        {!historyCollapsed && (
          <>
            {conversationsQuery.isError ? (
              <div className="nav-error" role="alert">
                {friendlyApiError(conversationsQuery.error, "加载会话")}
              </div>
            ) : filteredConversations.length === 0 ? (
              <div className="nav-empty">
                {searchKeyword ? "无匹配会话" : "暂无会话"}
              </div>
            ) : (
              filteredConversations.map((conv) => (
                <button
                  key={conv.id}
                  type="button"
                  className={`conv-item ${
                    currentConversationId === conv.id ? "current" : ""
                  }`}
                  onClick={() =>
                    handleSelectConversation(conv.id, conv.document_ids)
                  }
                >
                  <span className="item-text">
                    <span className="title">{conv.title ?? "未命名会话"}</span>
                    <span className="item-meta">
                      {formatConversationDate(conv.created_at)} · {conv.document_ids?.length ?? 0}{" "}
                      篇文档
                    </span>
                  </span>
                  <span
                    role="button"
                    tabIndex={0}
                    className="delete-btn"
                    aria-label="删除会话"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteConversation(conv.id);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        handleDeleteConversation(conv.id);
                      }
                    }}
                  >
                    ×
                  </span>
                </button>
              ))
            )}
          </>
        )}
      </nav>

      {/* 文档库 */}
      <nav className="nav-section" data-testid="nav-documents">
        <button
          type="button"
          className="nav-section-title"
          onClick={() => setDocumentsCollapsed((v) => !v)}
        >
          <span>文档库</span>
          <span className="arrow" aria-hidden="true">
            {documentsCollapsed ? "▸" : "▾"}
          </span>
        </button>
        {!documentsCollapsed && (
          <>
            {documentsQuery.isError ? (
              <div className="nav-error" role="alert">
                {friendlyApiError(documentsQuery.error, "加载文档")}
              </div>
            ) : documents.length === 0 ? (
              <div className="nav-empty">暂无文档</div>
            ) : (
              documents.map((doc) => (
                <div
                  key={doc.id}
                  className={`doc-item ${
                    currentDocumentIds.includes(doc.id) ? "selected" : ""
                  }`}
                  title={doc.original_name}
                >
                  <input
                    type="checkbox"
                    className="doc-select"
                    checked={currentDocumentIds.includes(doc.id)}
                    disabled={doc.status !== "ready"}
                    onChange={() => handleToggleDocument(doc.id)}
                    aria-label={`选择文档 ${doc.original_name}`}
                  />
                  <span className="item-text">
                    <span className="title">{doc.original_name}</span>
                    <span className="item-meta">
                      {doc.page_count} 页 · {DOCUMENT_STATUS_LABELS[doc.status]}
                    </span>
                    {doc.status === "failed" && doc.error_message && (
                      <span className="doc-error" title={doc.error_message}>
                        {doc.error_message}
                      </span>
                    )}
                  </span>
                  <span
                    className={`doc-status ${doc.status}`}
                    aria-label={DOCUMENT_STATUS_LABELS[doc.status]}
                  >
                    {doc.status === "ready"
                      ? "✓"
                      : doc.status === "processing"
                        ? "…"
                        : doc.status === "failed"
                          ? "!"
                          : "•"}
                  </span>
                  <button
                    type="button"
                    className="delete-btn"
                    aria-label="删除文档"
                    onClick={() => handleDeleteDocument(doc.id)}
                  >
                    ×
                  </button>
                </div>
              ))
            )}
          </>
        )}
      </nav>

      {/* 下层：设置 + 帮助 */}
      <div className="sidebar-footer" data-testid="sidebar-footer">
        <button
          type="button"
          className={`footer-btn ${activeView === "settings" ? "active" : ""}`}
          data-testid="footer-settings"
          onClick={() => handleFooterClick("settings")}
        >
          <span className="icon" aria-hidden="true">
            ⚙
          </span>
          <span>设置</span>
        </button>
        <button
          type="button"
          className={`footer-btn ${activeView === "help" ? "active" : ""}`}
          data-testid="footer-help"
          onClick={() => handleFooterClick("help")}
        >
          <span className="icon" aria-hidden="true">
            ?
          </span>
          <span>帮助</span>
        </button>
      </div>
    </aside>
  );
}
