import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import { friendlyApiError } from "../api/errors";
import type { FeedbackRating, MessageRead } from "../api/types";
import { useConversationDetail } from "./useConversationDetail";
import {
  CONVERSATIONS_QUERY_KEY,
  useCreateConversation,
  useDeleteConversation,
} from "./useConversations";
import {
  useDeleteFeedback,
  useFeedbackRatings,
  useSubmitFeedback,
} from "./useFeedback";

interface UseChatOptions {
  client: ApiClient;
  currentConversationId: string | null;
  setCurrentConversationId: (id: string | null) => void;
  currentDocumentIds: string[];
  setCurrentDocumentIds: (ids: string[]) => void;
}

export function useChat({
  client,
  currentConversationId,
  setCurrentConversationId,
  currentDocumentIds,
  setCurrentDocumentIds,
}: UseChatOptions) {
  const queryClient = useQueryClient();
  const conversationQuery = useConversationDetail(
    client,
    currentConversationId,
  );
  const createConversation = useCreateConversation(client);
  const deleteConversation = useDeleteConversation(client);
  const submitFeedback = useSubmitFeedback(client);
  const deleteFeedback = useDeleteFeedback(client);
  const [messages, setMessages] = useState<MessageRead[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const streamConversationIdRef = useRef<string | null>(null);
  const previousConversationIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (previousConversationIdRef.current !== currentConversationId) {
      previousConversationIdRef.current = currentConversationId;
      if (
        isStreaming &&
        streamConversationIdRef.current !== currentConversationId
      ) {
        const controller = abortControllerRef.current;
        abortControllerRef.current = null;
        streamConversationIdRef.current = null;
        controller?.abort();
        setIsStreaming(false);
        setMessages([]);
      } else if (!isStreaming) {
        setMessages([]);
      }
    }
  }, [currentConversationId, isStreaming]);

  useEffect(() => {
    if (
      isStreaming &&
      (!conversationQuery.data ||
        conversationQuery.data.id === streamConversationIdRef.current)
    ) {
      return;
    }
    if (conversationQuery.data?.messages) {
      setMessages(conversationQuery.data.messages);
    } else if (!currentConversationId) {
      setMessages([]);
    }
  }, [conversationQuery.data, currentConversationId]);

  useEffect(() => {
    if (conversationQuery.error) {
      setToastMessage(
        friendlyApiError(conversationQuery.error, "加载会话"),
      );
    }
  }, [conversationQuery.error]);

  const requestIds = messages.flatMap((message) =>
    message.role === "assistant" && message.request_id
      ? [message.request_id]
      : [],
  );
  const feedbackRatings = useFeedbackRatings(client, requestIds);

  const sendMessage = async (text: string) => {
    if (isStreaming) return;

    const timestamp = Date.now();
    const userMessage: MessageRead = {
      id: `local-user-${timestamp}`,
      role: "user",
      content: text,
      citations: null,
      request_id: null,
      created_at: new Date().toISOString(),
    };
    const assistantMessage: MessageRead = {
      id: `local-assistant-${timestamp}`,
      role: "assistant",
      content: "",
      citations: null,
      request_id: null,
      created_at: new Date().toISOString(),
    };
    setMessages((previous) => [
      ...previous,
      userMessage,
      assistantMessage,
    ]);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    streamConversationIdRef.current = currentConversationId;

    let conversationId = currentConversationId;
    if (!conversationId) {
      try {
        const conversation = await createConversation.mutateAsync({
          document_ids: currentDocumentIds.length
            ? currentDocumentIds
            : null,
        });
        if (controller.signal.aborted) return;
        conversationId = conversation.id;
        streamConversationIdRef.current = conversation.id;
        setCurrentConversationId(conversation.id);
      } catch (error) {
        if (controller.signal.aborted) return;
        setToastMessage(friendlyApiError(error, "创建会话"));
        setMessages((previous) => previous.slice(0, -2));
        setIsStreaming(false);
        abortControllerRef.current = null;
        streamConversationIdRef.current = null;
        return;
      }
    }

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
            if (controller.signal.aborted) return;
            setMessages((previous) => {
              const next = [...previous];
              const index = next.length - 1;
              if (next[index]?.role === "assistant") {
                next[index] = {
                  ...next[index],
                  content: next[index].content + token,
                };
              }
              return next;
            });
          },
          onDone: (data) => {
            if (controller.signal.aborted) return;
            setMessages((previous) => {
              const next = [...previous];
              const index = next.length - 1;
              if (next[index]?.role === "assistant") {
                next[index] = {
                  ...next[index],
                  id: data.message_id ?? next[index].id,
                  citations: data.citations,
                  request_id: data.request_id,
                };
              }
              return next;
            });
            void queryClient.invalidateQueries({
              queryKey: CONVERSATIONS_QUERY_KEY,
            });
          },
          onError: (message) => {
            if (controller.signal.aborted) return;
            setToastMessage(`生成失败：${message}`);
            setMessages((previous) => {
              const next = [...previous];
              const index = next.length - 1;
              if (next[index]?.role === "assistant") {
                next[index] = {
                  ...next[index],
                  content: `回答失败：${message}`,
                };
              }
              return next;
            });
          },
        },
        controller.signal,
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        if (abortControllerRef.current === controller) {
          setToastMessage("已停止生成");
        }
      } else {
        const message = friendlyApiError(error, "请求");
        setToastMessage(message);
        setMessages((previous) => {
          const next = [...previous];
          const index = next.length - 1;
          if (next[index]?.role === "assistant") {
            next[index] = { ...next[index], content: message };
          }
          return next;
        });
      }
    } finally {
      if (abortControllerRef.current === controller) {
        setIsStreaming(false);
        abortControllerRef.current = null;
        streamConversationIdRef.current = null;
      }
    }
  };

  const stopStream = () => {
    abortControllerRef.current?.abort();
  };

  const handleFeedback = (
    requestId: string,
    messageId: string,
    currentRating: FeedbackRating | null,
    newRating: FeedbackRating,
    comment?: string,
  ) => {
    if (currentRating === newRating) {
      deleteFeedback.mutate(requestId, {
        onSuccess: () => setToastMessage("已取消反馈"),
        onError: (error) =>
          setToastMessage(friendlyApiError(error, "取消反馈")),
      });
      return;
    }

    submitFeedback.mutate(
      {
        request_id: requestId,
        rating: newRating,
        message_id: messageId,
        comment,
      },
      {
        onSuccess: () =>
          setToastMessage(newRating === "like" ? "已点赞" : "已点踩"),
        onError: (error) =>
          setToastMessage(friendlyApiError(error, "提交反馈")),
      },
    );
  };

  const deleteCurrentConversation = () => {
    if (!currentConversationId) return;
    deleteConversation.mutate(currentConversationId, {
      onSuccess: () => {
        setCurrentConversationId(null);
        setCurrentDocumentIds([]);
        setMessages([]);
      },
      onError: (error) =>
        setToastMessage(friendlyApiError(error, "删除会话")),
    });
  };

  return {
    conversationDetail: conversationQuery.data,
    messages,
    isStreaming,
    feedbackRatings,
    toastMessage,
    setToastMessage,
    sendMessage,
    stopStream,
    handleFeedback,
    deleteCurrentConversation,
  };
}
