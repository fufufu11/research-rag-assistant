import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";

const DOCUMENTS_QUERY_KEY = ["documents"] as const;

export function useDocuments(client: ApiClient) {
  return useQuery({
    queryKey: DOCUMENTS_QUERY_KEY,
    queryFn: () => client.listDocuments(),
  });
}

export function useUploadDocument(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => client.uploadDocument(file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_QUERY_KEY });
    },
  });
}

export function useDeleteDocument(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => client.deleteDocument(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_QUERY_KEY });
    },
  });
}
