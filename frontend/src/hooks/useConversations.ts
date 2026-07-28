import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { ConversationCreate } from "../api/types";

export const CONVERSATIONS_QUERY_KEY = ["conversations"] as const;

export function useConversations(client: ApiClient) {
  return useQuery({
    queryKey: CONVERSATIONS_QUERY_KEY,
    queryFn: () => client.listConversations(),
  });
}

export function useCreateConversation(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConversationCreate) =>
      client.createConversation(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: CONVERSATIONS_QUERY_KEY,
      });
    },
  });
}

export function useDeleteConversation(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteConversation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: CONVERSATIONS_QUERY_KEY,
      });
    },
  });
}
