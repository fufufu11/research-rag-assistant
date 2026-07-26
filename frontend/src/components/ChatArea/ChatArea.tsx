import { useEffect, useRef, useState } from "react";
import { useApp } from "../../store/AppContext";
import {
  useConversationDetail,
  useCreateConversation,
  useDeleteConversation,
  useSubmitFeedback,
  useDeleteFeedback,
  useUploadDocument,
  useConversations,
} from "../../api/queries";
import type { MessageRead, FeedbackRating } from "../../api/types";
import { ModelDropdown } from "../ModelDropdown/ModelDropdown";
import { MessageItem } from "../Message/MessageItem";
import { InputBar } from "../InputBar/InputBar";
import { Toast } from "../Toast/Toast";

// ChatArea：右侧主聊天区
// 整合 T5（SSE 流式 + 消息渲染 + 引用卡片 + 复制 + 输入栏）+
// T6（历史消息加载 + 多轮对话 + conversation_id 透传）+
// T7（反馈按钮 + 历史消息反馈状态回填 + 切换/取消）
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function ChatArea() {
  const {
    client,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  } = useApp();

  const { data: conversationDetail } = useConversationDetail(
    client,
    currentConversationId,
  );
  const createConversation = useCreateConversation(client);
  const deleteConversation = useDeleteConversation(client);
  const uploadDocument = useUploadDocument(client);
  const conversationsQuery = useConversations(client);
  const submitFeedback = useSubmitFeedback(client);
  const deleteFeedback = useDeleteFeedback(client);

  // 本地维护的消息列表（包含历史 + 流式中临时消息）
  const [messages, setMessages] = useState<MessageRead[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingMessageIndex, setStreamingMessageIndex] = useState<
    number | null
  >(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // 当切换会话时，用历史消息替换本地消息列表
  useEffect(() => {
    // 新会话创建后，历史查询可能先于首轮流式回答完成并返回空消息。
    // 流式期间保留本地乐观消息，避免空历史覆盖正在生成的内容。
    if (isStreaming) return;
    if (conversationDetail?.messages) {
      setMessages(conversationDetail.messages);
    } else if (!currentConversationId) {
      // 切换到"新对话"时清空
      setMessages([]);
    }
  }, [conversationDetail, currentConversationId]);

  // 自动滚动到底部
  const messagesEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, isStreaming]);

  // 反馈状态：用 request_id 缓存当前会话每条 assistant 消息的反馈
  const [feedbackCache, setFeedbackCache] = useState<
    Record<string, FeedbackRating | null>
  >({});

  // 加载已有反馈
  const activeRequestIds = messages
    .filter((m) => m.role === "assistant" && m.request_id)
    .map((m) => m.request_id as string);

  // 每个未缓存的 request_id 触发一次反馈查询
  // 简化：用单次查询每个 requestId 的方式（useFeedback 不能在循环中调 hook）
  // 实际项目可批量接口；这里用 useEffect 拉取
  useEffect(() => {
    let cancelled = false;
    for (const requestId of activeRequestIds) {
      if (feedbackCache[requestId] !== undefined) continue;
      void client.getFeedback(requestId).then((fb) => {
        if (cancelled) return;
        setFeedbackCache((prev) => ({
          ...prev,
          [requestId]: fb?.rating ?? null,
        }));
      });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRequestIds.join(",")]);

  const handleSendMessage = async (text: string) => {
    if (isStreaming) return;

    // 1. 立即追加 user 消息到本地
    const userMsg: MessageRead = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content: text,
      citations: null,
      request_id: null,
      created_at: new Date().toISOString(),
    };
    const assistantMsg: MessageRead = {
      id: `local-assistant-${Date.now()}`,
      role: "assistant",
      content: "",
      citations: null,
      request_id: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setStreamingMessageIndex(messages.length + 1); // 指向 assistant 临时消息
    setIsStreaming(true);

    // 2. 若无 currentConversationId，懒创建会话
    let conversationId = currentConversationId;
    if (!conversationId) {
      try {
        const conv = await createConversation.mutateAsync({
          title: text.slice(0, 30),
          document_ids: currentDocumentIds.length
            ? currentDocumentIds
            : null,
        });
        conversationId = conv.id;
        setCurrentConversationId(conv.id);
        // 失效会话列表
        void conversationsQuery.refetch();
      } catch (err) {
        setToastMessage(
          `会话创建失败：${err instanceof Error ? err.message : "未知错误"}`,
        );
        setMessages((prev) => prev.slice(0, -2));
        setIsStreaming(false);
        setStreamingMessageIndex(null);
        return;
      }
    }

    // 3. 发起 SSE 流式问答
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await client.askQuestionStream(
        {
          question: text,
          document_ids: currentDocumentIds.length
            ? currentDocumentIds
            : undefined,
          conversation_id: conversationId,
          stream: true,
        },
        {
          onToken: (token) => {
            setMessages((prev) => {
              const next = [...prev];
              const idx = next.length - 1;
              if (next[idx]?.role === "assistant") {
                next[idx] = {
                  ...next[idx],
                  content: next[idx].content + token,
                };
              }
              return next;
            });
          },
          onDone: (data) => {
            setMessages((prev) => {
              const next = [...prev];
              const idx = next.length - 1;
              if (next[idx]?.role === "assistant") {
                next[idx] = {
                  ...next[idx],
                  citations: data.citations,
                  request_id: data.request_id,
                };
              }
              return next;
            });
            // 失效会话列表（title 可能更新）
            void conversationsQuery.refetch();
          },
          onError: (message) => {
            setToastMessage(`生成失败：${message}`);
            setMessages((prev) => {
              const next = [...prev];
              const idx = next.length - 1;
              if (next[idx]?.role === "assistant") {
                next[idx] = {
                  ...next[idx],
                  content: `回答失败：${message}`,
                };
              }
              return next;
            });
          },
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setToastMessage("已停止生成");
      } else {
        setToastMessage(
          `请求失败：${err instanceof Error ? err.message : "未知错误"}`,
        );
        setMessages((prev) => {
          const next = [...prev];
          const idx = next.length - 1;
          if (next[idx]?.role === "assistant") {
            next[idx] = {
              ...next[idx],
              content: `请求失败：${
                err instanceof Error ? err.message : "未知错误"
              }`,
            };
          }
          return next;
        });
      }
    } finally {
      setIsStreaming(false);
      setStreamingMessageIndex(null);
      abortControllerRef.current = null;
    }
  };

  const handleStopStream = () => {
    abortControllerRef.current?.abort();
  };

  const handleUploadFile = (file: File) => {
    uploadDocument.mutate(file, {
      onSuccess: (doc) => {
        // 新上传文档自动成为唯一问答范围，并开启新会话以锁定该范围。
        setCurrentConversationId(null);
        setCurrentDocumentIds([doc.id]);
        setMessages([]);
        setToastMessage(`已上传并选中「${doc.original_name}」`);
      },
      onError: (err) => {
        setToastMessage(
          `上传失败：${err instanceof Error ? err.message : "未知错误"}`,
        );
      },
    });
  };

  const handleFeedback = (
    requestId: string,
    currentRating: FeedbackRating | null,
    newRating: FeedbackRating,
  ) => {
    // 切换：相同 rating 则取消
    if (currentRating === newRating) {
      deleteFeedback.mutate(requestId, {
        onSuccess: () => {
          setFeedbackCache((prev) => ({ ...prev, [requestId]: null }));
          setToastMessage("已取消反馈");
        },
      });
    } else {
      submitFeedback.mutate(
        {
          request_id: requestId,
          rating: newRating,
        },
        {
          onSuccess: () => {
            setFeedbackCache((prev) => ({
              ...prev,
              [requestId]: newRating,
            }));
            setToastMessage(
              newRating === "like" ? "已点赞" : "已点踩",
            );
          },
        },
      );
    }
  };

  const handleDeleteConversation = () => {
    if (!currentConversationId) return;
    if (!window.confirm("确认删除当前会话？")) return;
    deleteConversation.mutate(currentConversationId, {
      onSuccess: () => {
        setCurrentConversationId(null);
        setCurrentDocumentIds([]);
        setMessages([]);
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
              onClick={handleDeleteConversation}
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
              {conversationDetail?.title ?? "未命名会话"} · 共{" "}
              {messages.length} 条消息
            </button>
          ) : (
            <span>未选择会话</span>
          )}
        </div>
      </div>

      <div className="messages-wrap">
        {messages.length === 0 ? (
          <div className="content-placeholder" data-testid="content-placeholder">
            <h1>科研文献智能问答</h1>
            <p>从左侧选择或新建对话开始</p>
          </div>
        ) : (
          <div className="messages" data-testid="messages">
            {messages.map((msg, i) => {
              const isStreamingThis =
                isStreaming && streamingMessageIndex === i;
              const feedbackRating = msg.request_id
                ? feedbackCache[msg.request_id] ?? null
                : null;
              return (
                <MessageItem
                  key={msg.id}
                  message={msg}
                  isStreaming={isStreamingThis}
                  feedbackRating={feedbackRating}
                  onFeedback={
                    msg.request_id
                      ? (rating) =>
                          handleFeedback(
                            msg.request_id as string,
                            feedbackRating,
                            rating,
                          )
                      : undefined
                  }
                  onCopy={() => setToastMessage("已复制到剪贴板")}
                />
              );
            })}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <InputBar
        onSubmit={isStreaming ? handleStopStream : handleSendMessage}
        onUploadFile={handleUploadFile}
        isStreaming={isStreaming}
        isUploading={uploadDocument.isPending}
      />

      <Toast message={toastMessage} onClose={() => setToastMessage(null)} />
    </main>
  );
}
