import { useEffect, useRef } from "react";
import { friendlyApiError } from "../../api/errors";
import { useChat } from "../../hooks/useChat";
import { useUploadDocument } from "../../hooks/useDocuments";
import { useApp } from "../../store/AppContext";
import { ModelDropdown } from "../ModelDropdown/ModelDropdown";
import { MessageItem } from "../Message/MessageItem";
import { InputBar } from "../InputBar/InputBar";
import { Toast } from "../Toast/Toast";

export function ChatArea() {
  const {
    client,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  } = useApp();
  const chat = useChat({
    client,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  });
  const uploadDocument = useUploadDocument(client);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [chat.messages, chat.isStreaming]);

  const handleUploadFile = (file: File) => {
    uploadDocument.mutate(file, {
      onSuccess: (document) => {
        setCurrentConversationId(null);
        setCurrentDocumentIds([document.id]);
        chat.setToastMessage(`已上传并选中「${document.original_name}」`);
      },
      onError: (error) => {
        chat.setToastMessage(friendlyApiError(error, "上传文档"));
      },
    });
  };

  return (
    <main className="chat-area" data-testid="chat-area">
      <div className="top-bar" data-testid="top-bar">
        <ModelDropdown />
        <div className="conversation-meta">
          {currentConversationId ? (
            <button
              type="button"
              onClick={chat.deleteCurrentConversation}
              title="点击删除当前会话"
              style={{
                background: "transparent",
                border: "none",
                color: "inherit",
                cursor: "pointer",
                padding: 0,
                font: "inherit",
              }}
            >
              {chat.conversationDetail?.title ?? "未命名会话"} · 共{" "}
              {chat.messages.length} 条消息
            </button>
          ) : (
            <span>未选择会话</span>
          )}
        </div>
      </div>

      <div className="messages-wrap">
        {chat.messages.length === 0 ? (
          <div className="content-placeholder" data-testid="content-placeholder">
            <h1>科研文献智能问答</h1>
            <p>选择文档或新建对话开始</p>
          </div>
        ) : (
          <div className="messages" data-testid="messages">
            {chat.messages.map((message, index) => {
              const isStreamingMessage =
                chat.isStreaming &&
                index === chat.messages.length - 1 &&
                message.role === "assistant";
              const feedbackRating = message.request_id
                ? chat.feedbackRatings[message.request_id] ?? null
                : null;
              return (
                <MessageItem
                  key={message.id}
                  message={message}
                  isStreaming={isStreamingMessage}
                  feedbackRating={feedbackRating}
                  onFeedback={
                    message.request_id
                      ? (rating, comment) =>
                          chat.handleFeedback(
                            message.request_id as string,
                            message.id,
                            feedbackRating,
                            rating,
                            comment,
                          )
                      : undefined
                  }
                  onCopy={() => chat.setToastMessage("已复制到剪贴板")}
                />
              );
            })}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <InputBar
        onSubmit={chat.isStreaming ? chat.stopStream : chat.sendMessage}
        onUploadFile={handleUploadFile}
        isStreaming={chat.isStreaming}
        isUploading={uploadDocument.isPending}
      />

      <Toast
        message={chat.toastMessage}
        onClose={() => chat.setToastMessage(null)}
      />
    </main>
  );
}
