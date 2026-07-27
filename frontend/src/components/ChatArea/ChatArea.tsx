import { useRef, useState, type ChangeEvent } from "react";
import type { ApiClient } from "../../api/client";
import { friendlyApiError } from "../../api/errors";
import type { ConversationRead } from "../../api/types";
import { useUploadDocument } from "../../hooks/useDocuments";
import {
  conversationScopeLabel,
  conversationTitle,
} from "../../utils/conversation";
import { ModelDropdown } from "../ModelDropdown/ModelDropdown";

interface ChatAreaProps {
  client: ApiClient;
  currentConversation?: ConversationRead | null;
}

interface UploadNotice {
  kind: "success" | "error";
  message: string;
}

function isPdf(file: File): boolean {
  return (
    file.type === "application/pdf" ||
    (file.type === "" && file.name.toLowerCase().endsWith(".pdf"))
  );
}

export function ChatArea({ client, currentConversation = null }: ChatAreaProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<UploadNotice | null>(null);
  const uploadDocument = useUploadDocument(client);
  const conversationSummary = currentConversation
    ? `${conversationTitle(currentConversation)} · ${conversationScopeLabel(
        currentConversation,
      )}`
    : "未选择会话";

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!isPdf(file)) {
      setNotice({
        kind: "error",
        message: "上传文档失败：只支持单个 PDF 文件。",
      });
      return;
    }

    setNotice(null);
    uploadDocument.mutate(file, {
      onSuccess: (document) => {
        setNotice({
          kind: "success",
          message: `已上传 ${document.original_name}`,
        });
      },
      onError: (error) => {
        setNotice({
          kind: "error",
          message: friendlyApiError(error, "上传文档"),
        });
      },
    });
  };

  return (
    <main className="chat-area" data-testid="chat-area">
      <div className="top-bar" data-testid="top-bar">
        <ModelDropdown />
        <div
          className="conversation-meta"
          title={currentConversation ? conversationSummary : undefined}
        >
          <span>{conversationSummary}</span>
        </div>
      </div>

      <div className="messages-wrap">
        <div className="content-placeholder" data-testid="content-placeholder">
          <h1>科研文献智能问答</h1>
          <p>从左侧选择或新建对话开始</p>
        </div>
      </div>

      <div className="input-bar-wrap" data-testid="input-bar">
        <div className="input-bar">
          <input
            ref={fileInputRef}
            className="visually-hidden"
            type="file"
            accept="application/pdf,.pdf"
            aria-label="选择 PDF 文档"
            disabled={uploadDocument.isPending}
            onChange={handleFileChange}
          />
          <button
            type="button"
            className="input-icon-button upload-button"
            aria-label={uploadDocument.isPending ? "正在上传 PDF" : "上传 PDF"}
            title={uploadDocument.isPending ? "正在上传 PDF" : "上传 PDF"}
            disabled={uploadDocument.isPending}
            onClick={() => fileInputRef.current?.click()}
          >
            <span aria-hidden="true">+</span>
          </button>
          <textarea
            className="question-input"
            aria-label="问题输入"
            placeholder="输入问题…"
            rows={1}
            disabled
          />
          <button
            type="button"
            className="input-icon-button send-button"
            aria-label="发送"
            title="发送"
            disabled
          >
            <span aria-hidden="true">↑</span>
          </button>
        </div>
        {uploadDocument.isPending && uploadDocument.variables && (
          <p className="upload-notice" role="status">
            正在上传 {uploadDocument.variables.name}…
          </p>
        )}
        {!uploadDocument.isPending && notice && (
          <p
            className={`upload-notice ${notice.kind}`}
            role={notice.kind === "error" ? "alert" : "status"}
          >
            {notice.message}
          </p>
        )}
      </div>
    </main>
  );
}
