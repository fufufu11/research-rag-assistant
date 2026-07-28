import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";

export function useConversationDetail(client: ApiClient, id: string | null) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => client.getConversation(id as string),
    enabled: Boolean(id),
  });
}
