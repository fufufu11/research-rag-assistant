import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";

export function conversationQueryKey(id: string) {
  return ["conversation", id] as const;
}

export function useConversationDetail(client: ApiClient, id: string | null) {
  return useQuery({
    queryKey: conversationQueryKey(id ?? ""),
    queryFn: ({ signal }) => client.getConversation(id as string, signal),
    enabled: id !== null,
    refetchOnMount: "always",
  });
}
