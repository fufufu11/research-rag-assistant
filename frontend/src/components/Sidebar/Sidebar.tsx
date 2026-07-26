import { useState } from "react";
import { useApp } from "../../store/AppContext";
import {
  useConversations,
  useDeleteConversation,
  useDocuments,
  useDeleteDocument,
} from "../../api/queries";
import type { ActiveView } from "../../store/AppContext";

// Sidebar：左侧栏组件（260px 深棕背景）
// T3-T4：接入真实文档与会话列表（TanStack Query 缓存 + 失效）
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function Sidebar() {
  const {
    client,
    activeView,
    setActiveView,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  } = useApp();
  const [searchKeyword, setSearchKeyword] = useState("");
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [documentsCollapsed, setDocumentsCollapsed] = useState(false);

  const { data: conversationsData } = useConversations(client);
  const { data: documentsData } = useDocuments(client);
  const deleteConversation = useDeleteConversation(client);
  const deleteDocument = useDeleteDocument(client);

  const conversations = conversationsData?.items ?? [];
  const documents = documentsData?.items ?? [];

  const filteredConversations = searchKeyword
    ? conversations.filter((c) =>
        (c.title ?? "未命名会话").includes(searchKeyword),
      )
    : conversations;

  const handleNewChat = () => {
    // 立即清空当前会话，回到欢迎页；用户发送首条消息时再按需创建
    setCurrentConversationId(null);
    setCurrentDocumentIds([]);
    setActiveView("chat");
  };

  const handleSelectConversation = (id: string, docIds: string[] | null) => {
    setCurrentConversationId(id);
    setCurrentDocumentIds(docIds ?? []);
    setActiveView("chat");
  };

  const handleDeleteConversation = (id: string) => {
    if (!window.confirm("确认删除该会话？此操作不可撤销。")) return;
    deleteConversation.mutate(id, {
      onSuccess: () => {
        if (currentConversationId === id) {
          setCurrentConversationId(null);
          setCurrentDocumentIds([]);
        }
      },
    });
  };

  const handleDeleteDocument = (id: string, name: string) => {
    if (!window.confirm(`确认删除文档「${name}」？此操作不可撤销。`)) return;
    deleteDocument.mutate(id, {
      onSuccess: () => {
        if (currentDocumentIds.includes(id)) {
          setCurrentConversationId(null);
          setCurrentDocumentIds(
            currentDocumentIds.filter((documentId) => documentId !== id),
          );
        }
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
  };

  const handleFooterClick = (view: ActiveView) => {
    setActiveView(view);
  };

  return (
    <aside className="sidebar" data-testid="sidebar">
      <div className="sidebar-header">
        <span className="logo-dot" aria-hidden="true" />
        <span>
          research<span className="accent">·</span>rag
        </span>
      </div>

      <button
        type="button"
        className="new-chat-btn"
        data-testid="new-chat-btn"
        onClick={handleNewChat}
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
            {filteredConversations.length === 0 ? (
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
                  <span className="title">{conv.title ?? "未命名会话"}</span>
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
            {documents.length === 0 ? (
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
                  <span className="title">{doc.original_name}</span>
                  <span className={`doc-status ${doc.status}`}>
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
                    onClick={() =>
                      handleDeleteDocument(doc.id, doc.original_name)
                    }
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
