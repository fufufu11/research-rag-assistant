import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { MessageRead } from "../api/types";
import {
  conversationQueryKey,
  useConversationDetail,
} from "./useConversationDetail";
import { CONVERSATIONS_QUERY_KEY } from "./useConversations";

interface UseChatOptions {
  client: ApiClient;
  conversationId: string | null;
}

interface PendingTurn {
  question: string;
  userMessage: MessageRead;
  assistantMessage: MessageRead;
  phase: "streaming" | "syncing" | "sync-failed";
  syncError?: unknown;
}

export function useChat({ client, conversationId }: UseChatOptions) {
  const queryClient = useQueryClient();
  const conversationQuery = useConversationDetail(client, conversationId);
  const turns = useRef(new Map<string, PendingTurn>());
  const controllers = useRef(new Map<string, AbortController>());
  const generationErrors = useRef(new Map<string, unknown>());
  const sequence = useRef(0);
  const [, render] = useState(0);

  const publish = () => render((value) => value + 1);

  useEffect(
    () => () => {
      controllers.current.forEach((controller) => controller.abort());
      controllers.current.clear();
      turns.current.clear();
      generationErrors.current.clear();
    },
    [],
  );

  useEffect(() => {
    if (!conversationId || !conversationQuery.data) return;
    const turn = turns.current.get(conversationId);
    const requestId = turn?.assistantMessage.request_id;
    if (
      !turn ||
      turn.phase === "streaming" ||
      !requestId ||
      !conversationQuery.data.messages?.some(
        (message) => message.request_id === requestId,
      )
    ) {
      return;
    }
    const controller = controllers.current.get(conversationId);
    controllers.current.delete(conversationId);
    turns.current.delete(conversationId);
    controller?.abort();
    publish();
    void queryClient.invalidateQueries({
      queryKey: CONVERSATIONS_QUERY_KEY,
    });
  }, [conversationId, conversationQuery.data, queryClient]);

  const updateTurn = (
    id: string,
    update: (turn: PendingTurn) => PendingTurn,
  ) => {
    const turn = turns.current.get(id);
    if (!turn) return;
    turns.current.set(id, update(turn));
    publish();
  };

  const confirmPersistedTurn = async (
    id: string,
    requestId: string,
    signal?: AbortSignal,
  ) => {
    const detail = await client.getConversation(id, signal);
    if (signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const isPersisted = detail.messages?.some(
      (message) => message.request_id === requestId,
    );
    if (!isPersisted) {
      throw new Error("Persisted conversation is missing the completed turn");
    }
    queryClient.setQueryData(conversationQueryKey(id), detail);
    turns.current.delete(id);
    publish();
    void queryClient.invalidateQueries({
      queryKey: CONVERSATIONS_QUERY_KEY,
    });
  };

  const sendMessage = async (question: string): Promise<unknown | null> => {
    const targetConversationId = conversationId;
    if (
      !targetConversationId ||
      controllers.current.has(targetConversationId) ||
      turns.current.has(targetConversationId)
    ) {
      return null;
    }

    sequence.current += 1;
    const localId = `${targetConversationId}-${sequence.current}`;
    const createdAt = new Date().toISOString();
    const assistantId = `local-assistant-${localId}`;
    turns.current.set(targetConversationId, {
      question,
      phase: "streaming",
      userMessage: {
        id: `local-user-${localId}`,
        role: "user",
        content: question,
        citations: null,
        request_id: null,
        created_at: createdAt,
      },
      assistantMessage: {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: null,
        request_id: null,
        created_at: createdAt,
      },
    });
    generationErrors.current.delete(targetConversationId);
    publish();

    const controller = new AbortController();
    controllers.current.set(targetConversationId, controller);
    let completedRequestId: string | null = null;
    try {
      await client.askQuestionStream(
        { question, conversation_id: targetConversationId },
        {
          onToken: (text) => {
            if (controller.signal.aborted) return;
            updateTurn(targetConversationId, (turn) => ({
              ...turn,
              assistantMessage: {
                ...turn.assistantMessage,
                content: turn.assistantMessage.content + text,
              },
            }));
          },
          onDone: (event) => {
            if (controller.signal.aborted) return;
            completedRequestId = event.request_id;
            updateTurn(targetConversationId, (turn) => ({
              ...turn,
              phase: "syncing",
              assistantMessage: {
                ...turn.assistantMessage,
                id: event.message_id ?? turn.assistantMessage.id,
                citations: event.citations,
                request_id: event.request_id,
              },
            }));
          },
          onError: () => undefined,
        },
        controller.signal,
      );
      if (completedRequestId !== null) {
        await confirmPersistedTurn(
          targetConversationId,
          completedRequestId,
          controller.signal,
        );
      }
      return null;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return null;
      }
      if (completedRequestId !== null) {
        updateTurn(targetConversationId, (turn) => ({
          ...turn,
          phase: "sync-failed",
          syncError: error,
        }));
        return null;
      }
      turns.current.delete(targetConversationId);
      generationErrors.current.set(targetConversationId, error);
      publish();
      return error;
    } finally {
      if (controllers.current.get(targetConversationId) === controller) {
        controllers.current.delete(targetConversationId);
        publish();
      }
    }
  };

  const stopMessage = (): string | null => {
    if (!conversationId) return null;
    const turn = turns.current.get(conversationId);
    const controller = controllers.current.get(conversationId);
    if (!turn || !controller) return null;
    controllers.current.delete(conversationId);
    turns.current.delete(conversationId);
    controller.abort();
    publish();
    return turn.question;
  };

  const discardConversation = (id: string) => {
    const controller = controllers.current.get(id);
    controllers.current.delete(id);
    turns.current.delete(id);
    generationErrors.current.delete(id);
    controller?.abort();
    publish();
  };

  const retrySync = async () => {
    if (!conversationId) return;
    const turn = turns.current.get(conversationId);
    const requestId = turn?.assistantMessage.request_id;
    if (
      turn?.phase !== "sync-failed" ||
      !requestId ||
      controllers.current.has(conversationId)
    ) {
      return;
    }
    const controller = new AbortController();
    controllers.current.set(conversationId, controller);
    updateTurn(conversationId, (current) => ({
      ...current,
      phase: "syncing",
      syncError: undefined,
    }));
    try {
      await confirmPersistedTurn(conversationId, requestId, controller.signal);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      updateTurn(conversationId, (current) => ({
        ...current,
        phase: "sync-failed",
        syncError: error,
      }));
    } finally {
      if (controllers.current.get(conversationId) === controller) {
        controllers.current.delete(conversationId);
        publish();
      }
    }
  };

  const currentTurn = conversationId ? turns.current.get(conversationId) : null;
  const streamingConversationIds = new Set(
    [...turns.current.entries()]
      .filter(([, turn]) => turn.phase !== "sync-failed")
      .map(([id]) => id),
  );
  const failedConversationIds = new Set(
    [...turns.current.entries()]
      .filter(([, turn]) => turn.phase === "sync-failed")
      .map(([id]) => id),
  );
  const generationFailedConversationIds = new Set(
    generationErrors.current.keys(),
  );
  const serverMessages = conversationQuery.data?.messages ?? [];
  const currentTurnIsPersisted =
    currentTurn?.assistantMessage.request_id !== null &&
    currentTurn?.assistantMessage.request_id !== undefined &&
    serverMessages.some(
      (message) =>
        message.request_id === currentTurn.assistantMessage.request_id,
    );
  const messages = currentTurn && !currentTurnIsPersisted
    ? [...serverMessages, currentTurn.userMessage, currentTurn.assistantMessage]
    : serverMessages;
  const isStreaming = currentTurn?.phase === "streaming";
  const hasPendingTurn = currentTurn !== undefined;
  const isHistoryReady =
    conversationId !== null &&
    conversationQuery.isSuccess &&
    !conversationQuery.isFetching;
  const isHistoryLoading =
    conversationId !== null && conversationQuery.isPending;

  return {
    conversationDetail: conversationQuery.data ?? null,
    messages,
    isStreaming,
    isHistoryReady,
    isHistoryLoading,
    historyError: conversationQuery.error,
    hasPendingTurn,
    syncError:
      currentTurn?.phase === "sync-failed" ? currentTurn.syncError : null,
    generationError: conversationId
      ? (generationErrors.current.get(conversationId) ?? null)
      : null,
    streamingConversationIds,
    failedConversationIds,
    generationFailedConversationIds,
    reloadHistory: () => conversationQuery.refetch(),
    sendMessage,
    stopMessage,
    discardConversation,
    retrySync,
    dismissGenerationError: () => {
      if (!conversationId) return;
      generationErrors.current.delete(conversationId);
      publish();
    },
  };
}
