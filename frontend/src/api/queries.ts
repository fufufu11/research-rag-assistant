import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiClient } from "./client";
import type {
  ConversationCreate,
  FeedbackCreate,
  QueryRequest,
} from "./types";

// TanStack Query keys 命名约定：
// ["documents"] / ["conversations"] / ["conversation", id] / ["feedback", requestId]

// === 文档管理 hooks ===

export function useDocuments(client: ApiClient) {
  return useQuery({
    queryKey: ["documents"],
    queryFn: () => client.listDocuments(),
  });
}

export function useUploadDocument(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => client.uploadDocument(file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useDeleteDocument(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteDocument(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

// === 会话管理 hooks ===

export function useConversations(client: ApiClient) {
  return useQuery({
    queryKey: ["conversations"],
    queryFn: () => client.listConversations(),
  });
}

export function useConversationDetail(
  client: ApiClient,
  id: string | null,
) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => client.getConversation(id as string),
    enabled: !!id,
  });
}

export function useCreateConversation(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConversationCreate) =>
      client.createConversation(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useDeleteConversation(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteConversation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

// === 反馈 hooks ===

export function useFeedback(client: ApiClient, requestId: string | null) {
  return useQuery({
    queryKey: ["feedback", requestId],
    queryFn: () => client.getFeedback(requestId as string),
    enabled: !!requestId,
  });
}

export function useSubmitFeedback(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: FeedbackCreate) => client.submitFeedback(payload),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({
        queryKey: ["feedback", data.request_id],
      });
    },
  });
}

export function useDeleteFeedback(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (requestId: string) => client.deleteFeedback(requestId),
    onSuccess: (_data, requestId) => {
      void queryClient.invalidateQueries({
        queryKey: ["feedback", requestId],
      });
    },
  });
}

// === 问答（非 hook 版，供组件内部命令式调用） ===

export async function askQuestionOnce(
  client: ApiClient,
  payload: QueryRequest,
) {
  return client.askQuestion(payload);
}
