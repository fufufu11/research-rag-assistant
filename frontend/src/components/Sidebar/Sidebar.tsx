import { useState } from "react";
import type { ApiClient } from "../../api/client";
import { friendlyApiError } from "../../api/errors";
import type { ConversationRead, DocumentStatus } from "../../api/types";
import {
  useConversations,
  useCreateConversation,
  useDeleteConversation,
} from "../../hooks/useConversations";
import { useDeleteDocument, useDocuments } from "../../hooks/useDocuments";
import {
  conversationScopeLabel,
  conversationTitle,
} from "../../utils/conversation";

const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: "等待处理",
  processing: "处理中",
  ready: "就绪",
  failed: "失败",
};

interface SidebarProps {
  client: ApiClient;
  currentConversationId?: string | null;
  selectedDocumentIds?: string[];
  onSelectedDocumentIdsChange?: (ids: string[]) => void;
  onSelectConversation?: (conversation: ConversationRead) => void;
  onConversationCreated?: (conversation: ConversationRead) => void;
  onConversationDeleted?: (id: string) => void;
}

export function Sidebar({
  client,
  currentConversationId = null,
  selectedDocumentIds = [],
  onSelectedDocumentIdsChange,
  onSelectConversation,
  onConversationCreated,
  onConversationDeleted,
}: SidebarProps) {
  const [conversationsCollapsed, setConversationsCollapsed] = useState(false);
  const [documentsCollapsed, setDocumentsCollapsed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const documentsQuery = useDocuments(client);
  const deleteDocument = useDeleteDocument(client);
  const conversationsQuery = useConversations(client);
  const createConversation = useCreateConversation(client);
  const deleteConversation = useDeleteConversation(client);
  const documents = documentsQuery.data?.items ?? [];
  const conversations = conversationsQuery.data?.items ?? [];

  const handleDeleteDocument = (id: string) => {
    setActionError(null);
    deleteDocument.mutate(id, {
      onError: (error) => {
        setActionError(friendlyApiError(error, "删除文档"));
      },
    });
  };

  const handleDocumentSelection = (id: string, selected: boolean) => {
    onSelectedDocumentIdsChange?.(
      selected
        ? [...selectedDocumentIds, id]
        : selectedDocumentIds.filter((documentId) => documentId !== id),
    );
  };

  const handleCreateConversation = () => {
    setActionError(null);
    createConversation.mutate(
      { document_ids: selectedDocumentIds },
      {
        onSuccess: (conversation) => {
          onConversationCreated?.(conversation);
          onSelectConversation?.(conversation);
        },
        onError: (error) => {
          setActionError(friendlyApiError(error, "创建会话"));
        },
      },
    );
  };

  const handleDeleteConversation = (id: string) => {
    setActionError(null);
    deleteConversation.mutate(id, {
      onSuccess: () => onConversationDeleted?.(id),
      onError: (error) => {
        setActionError(friendlyApiError(error, "删除会话"));
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

      <button
        type="button"
        className="new-chat-btn"
        data-testid="new-chat-btn"
        disabled={createConversation.isPending}
        onClick={handleCreateConversation}
      >
        <span className="star" aria-hidden="true">
          ✦
        </span>
        <span>{createConversation.isPending ? "正在创建…" : "新建对话"}</span>
      </button>

      <input
        type="text"
        className="search-input"
        placeholder="搜索会话…"
        aria-label="搜索会话"
        data-testid="search-input"
      />

      {actionError && (
        <p className="nav-error sidebar-action-error" role="alert">
          {actionError}
        </p>
      )}

      <nav className="nav-section" data-testid="nav-history">
        <button
          type="button"
          className="nav-section-title"
          aria-label="历史会话"
          aria-controls="conversation-list"
          aria-expanded={!conversationsCollapsed}
          onClick={() =>
            setConversationsCollapsed((collapsed) => !collapsed)
          }
        >
          <span>历史会话</span>
          <span className="arrow" aria-hidden="true">
            {conversationsCollapsed ? "▸" : "▾"}
          </span>
        </button>
        {!conversationsCollapsed && (
          <div id="conversation-list">
            {conversationsQuery.isPending ? (
              <div className="nav-empty" role="status">
                正在加载会话…
              </div>
            ) : conversationsQuery.isError ? (
              <p className="nav-error" role="alert">
                {friendlyApiError(conversationsQuery.error, "加载会话")}
              </p>
            ) : conversations.length === 0 ? (
              <div className="nav-empty">暂无会话</div>
            ) : (
              <ul className="conversation-list">
                {conversations.map((conversation) => (
                  <li className="conversation-row" key={conversation.id}>
                    <button
                      type="button"
                      className="conversation-item"
                      aria-label={conversationTitle(conversation)}
                      aria-current={
                        currentConversationId === conversation.id
                          ? "page"
                          : undefined
                      }
                      onClick={() => onSelectConversation?.(conversation)}
                    >
                      <span className="conversation-title">
                        {conversationTitle(conversation)}
                      </span>
                      <span className="conversation-details">
                        <span>
                          {conversation.created_at
                            .slice(0, 10)
                            .replaceAll("-", "/")}
                        </span>
                        <span>{conversationScopeLabel(conversation)}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="conversation-delete"
                      aria-label={`删除会话 ${conversationTitle(conversation)}`}
                      title="删除会话"
                      disabled={
                        deleteConversation.isPending &&
                        deleteConversation.variables === conversation.id
                      }
                      onClick={() => handleDeleteConversation(conversation.id)}
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
                    <input
                      type="checkbox"
                      className="document-select"
                      aria-label={`选择文档 ${document.original_name}`}
                      checked={selectedDocumentIds.includes(document.id)}
                      disabled={document.status !== "ready"}
                      onChange={(event) =>
                        handleDocumentSelection(
                          document.id,
                          event.currentTarget.checked,
                        )
                      }
                    />
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
