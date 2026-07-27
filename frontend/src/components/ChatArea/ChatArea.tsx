import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
import { Send, Square, Upload, X } from "lucide-react";
import type { ApiClient } from "../../api/client";
import { friendlyApiError, friendlyChatError } from "../../api/errors";
import type { ConversationRead } from "../../api/types";
import { useUploadDocument } from "../../hooks/useDocuments";
import { useChat } from "../../hooks/useChat";
import {
  conversationScopeLabel,
  conversationTitle,
} from "../../utils/conversation";
import { ModelDropdown } from "../ModelDropdown/ModelDropdown";
import { MessageItem } from "../Message/MessageItem";

interface ChatAreaProps {
  client: ApiClient;
  currentConversation?: ConversationRead | null;
  canChat?: boolean;
}

type ChatCoordinator = ReturnType<typeof useChat>;

interface ChatAreaViewProps extends ChatAreaProps {
  chat: ChatCoordinator;
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

export function ChatArea({
  client,
  currentConversation = null,
  canChat = false,
}: ChatAreaProps) {
  const chat = useChat({
    client,
    conversationId: currentConversation?.id ?? null,
  });

  return (
    <ChatAreaView
      client={client}
      currentConversation={currentConversation}
      canChat={canChat}
      chat={chat}
    />
  );
}

export function ChatAreaView({
  client,
  currentConversation = null,
  canChat = false,
  chat,
}: ChatAreaViewProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const previousConversationId = useRef(currentConversation?.id ?? null);
  const activeConversationId = useRef(currentConversation?.id ?? null);
  const drafts = useRef(new Map<string, string>());
  const [notice, setNotice] = useState<UploadNotice | null>(null);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const uploadDocument = useUploadDocument(client);
  const displayConversation = chat.conversationDetail ?? currentConversation;
  const conversationSummary = displayConversation
    ? `${conversationTitle(displayConversation)} · ${conversationScopeLabel(
        displayConversation,
      )}`
    : "未选择会话";
  activeConversationId.current = currentConversation?.id ?? null;

  useEffect(() => {
    const nextId = currentConversation?.id ?? null;
    if (previousConversationId.current === nextId) return;
    previousConversationId.current = nextId;
    setQuestion(nextId ? (drafts.current.get(nextId) ?? "") : "");
    setCopyNotice(null);
  }, [currentConversation?.id]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({
      behavior: "smooth",
      block: "end",
    });
  }, [chat.messages, chat.isStreaming]);

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

  const sendQuestion = () => {
    const value = question.trim();
    if (!canChat || !chat.isHistoryReady || !value || chat.hasPendingTurn) return;
    const targetConversationId = currentConversation?.id ?? null;
    setQuestion("");
    if (targetConversationId) drafts.current.delete(targetConversationId);
    void chat.sendMessage(value).then((error) => {
      if (error === null || !targetConversationId) return;
      drafts.current.set(targetConversationId, value);
      if (activeConversationId.current === targetConversationId) {
        setQuestion(value);
      }
    });
  };

  const handleQuestionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendQuestion();
    }
  };

  const stopQuestion = () => {
    const interruptedQuestion = chat.stopMessage();
    if (interruptedQuestion !== null) {
      setQuestion(interruptedQuestion);
      if (currentConversation) {
        drafts.current.set(currentConversation.id, interruptedQuestion);
      }
    }
  };

  return (
    <main className="chat-area" data-testid="chat-area">
      <div className="top-bar" data-testid="top-bar">
        <ModelDropdown />
        <div
          className="conversation-meta"
          title={displayConversation ? conversationSummary : undefined}
        >
          <span>{conversationSummary}</span>
        </div>
      </div>

      <div className="messages-wrap">
        {chat.isHistoryLoading ? (
          <div className="content-status" role="status">
            正在加载会话…
          </div>
        ) : chat.historyError ? (
          <div className="content-status history-error">
            <p role="alert">{friendlyApiError(chat.historyError, "加载会话")}</p>
            <button type="button" onClick={() => void chat.reloadHistory()}>
              重试加载
            </button>
          </div>
        ) : chat.messages.length === 0 ? (
          <div className="content-placeholder" data-testid="content-placeholder">
            <h1>科研文献智能问答</h1>
            <p>从左侧选择或新建对话开始</p>
          </div>
        ) : (
          <div className="messages" aria-live="polite">
            {chat.messages.map((message, index) => (
              <MessageItem
                message={message}
                key={message.id}
                isStreaming={
                  chat.isStreaming &&
                  message.role === "assistant" &&
                  index === chat.messages.length - 1
                }
                onCopied={() => {
                  setCopyNotice("回答与来源已复制");
                  window.setTimeout(() => setCopyNotice(null), 1800);
                }}
              />
            ))}
            <div ref={messagesEndRef} aria-hidden="true" />
          </div>
        )}
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
            <Upload aria-hidden="true" size={18} />
          </button>
          <textarea
            className="question-input"
            aria-label="问题输入"
            placeholder="输入问题…"
            rows={1}
            value={question}
            disabled={!canChat || !chat.isHistoryReady || chat.hasPendingTurn}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setQuestion(value);
              if (currentConversation) drafts.current.set(currentConversation.id, value);
            }}
            onKeyDown={handleQuestionKeyDown}
          />
          <button
            type="button"
            className="input-icon-button send-button"
            aria-label={chat.isStreaming ? "停止生成" : "发送"}
            title={chat.isStreaming ? "停止生成" : "发送"}
            disabled={
              chat.isStreaming
                ? false
                : !canChat ||
                  !chat.isHistoryReady ||
                  chat.hasPendingTurn ||
                  !question.trim()
            }
            onClick={chat.isStreaming ? stopQuestion : sendQuestion}
          >
            {chat.isStreaming ? (
              <Square aria-hidden="true" size={16} fill="currentColor" />
            ) : (
              <Send aria-hidden="true" size={18} />
            )}
          </button>
        </div>
        <p className="chat-disclaimer">AI 可能出错，请核查重要信息</p>
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
        {chat.generationError !== null && (
          <div className="chat-error" role="alert">
            <span>{friendlyChatError(chat.generationError)}</span>
            <button
              type="button"
              aria-label="关闭错误"
              onClick={chat.dismissGenerationError}
            >
              <X aria-hidden="true" size={16} />
            </button>
          </div>
        )}
        {chat.syncError !== null && (
          <div className="chat-error sync-error">
            <span role="alert">
              {friendlyApiError(chat.syncError, "同步会话")}
            </span>
            <button type="button" onClick={() => void chat.retrySync()}>
              重试同步
            </button>
          </div>
        )}
        {copyNotice && (
          <p className="copy-notice" role="status">
            {copyNotice}
          </p>
        )}
      </div>
    </main>
  );
}
