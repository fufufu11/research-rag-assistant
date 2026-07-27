import { useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import type { MessageRead } from "../api/types";

interface UseChatOptions {
  client: ApiClient;
  conversationId: string | null;
}

interface ActiveRequest {
  controller: AbortController;
  conversationId: string;
  question: string;
  messageCount: number;
}

export function useChat({ client, conversationId }: UseChatOptions) {
  const transcripts = useRef(new Map<string, MessageRead[]>());
  const currentConversationId = useRef(conversationId);
  const activeRequest = useRef<ActiveRequest | null>(null);
  const sequence = useRef(0);
  const [messages, setMessages] = useState<MessageRead[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const updateTranscript = (
    id: string,
    update: (messages: MessageRead[]) => MessageRead[],
  ) => {
    const next = update(transcripts.current.get(id) ?? []);
    transcripts.current.set(id, next);
    if (currentConversationId.current === id) setMessages(next);
  };

  const rollback = (request: ActiveRequest) => {
    updateTranscript(request.conversationId, (current) =>
      current.slice(0, request.messageCount),
    );
  };

  useEffect(() => {
    if (currentConversationId.current === conversationId) return;
    const request = activeRequest.current;
    if (request) {
      activeRequest.current = null;
      request.controller.abort();
      rollback(request);
    }
    currentConversationId.current = conversationId;
    setMessages(
      conversationId ? (transcripts.current.get(conversationId) ?? []) : [],
    );
    setIsStreaming(false);
  }, [conversationId]);

  const sendMessage = async (question: string): Promise<unknown | null> => {
    const targetConversationId = conversationId;
    if (!targetConversationId || isStreaming) return null;

    sequence.current += 1;
    const localId = `${targetConversationId}-${sequence.current}`;
    const createdAt = new Date().toISOString();
    const assistantId = `local-assistant-${localId}`;
    const existing = transcripts.current.get(targetConversationId) ?? [];
    updateTranscript(targetConversationId, (current) => [
      ...current,
      {
        id: `local-user-${localId}`,
        role: "user",
        content: question,
        citations: null,
        request_id: null,
        created_at: createdAt,
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: null,
        request_id: null,
        created_at: createdAt,
      },
    ]);
    setIsStreaming(true);
    const controller = new AbortController();
    const request: ActiveRequest = {
      controller,
      conversationId: targetConversationId,
      question,
      messageCount: existing.length,
    };
    activeRequest.current = request;

    try {
      await client.askQuestionStream(
        { question, conversation_id: targetConversationId },
        {
          onToken: (text) => {
            if (controller.signal.aborted) return;
            updateTranscript(targetConversationId, (current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, content: message.content + text }
                  : message,
              ),
            );
          },
          onDone: (event) => {
            if (controller.signal.aborted) return;
            updateTranscript(targetConversationId, (current) =>
              current.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      id: event.message_id ?? message.id,
                      citations: event.citations,
                      request_id: event.request_id,
                    }
                  : message,
              ),
            );
          },
          onError: () => undefined,
        },
        controller.signal,
      );
      return null;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return null;
      }
      rollback(request);
      return error;
    } finally {
      if (activeRequest.current?.controller === controller) {
        activeRequest.current = null;
        setIsStreaming(false);
      }
    }
  };

  const stopMessage = (): string | null => {
    const request = activeRequest.current;
    if (!request) return null;
    activeRequest.current = null;
    request.controller.abort();
    rollback(request);
    setIsStreaming(false);
    return request.question;
  };

  return { messages, isStreaming, sendMessage, stopMessage };
}
