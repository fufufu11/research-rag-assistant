import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { FeedbackCreate, FeedbackRating } from "../api/types";

const feedbackQueryKey = (requestId: string | null) =>
  ["feedback", requestId] as const;

export function useFeedback(client: ApiClient, requestId: string | null) {
  return useQuery({
    queryKey: feedbackQueryKey(requestId),
    queryFn: () => client.getFeedback(requestId as string),
    enabled: Boolean(requestId),
  });
}

export function useFeedbackRatings(
  client: ApiClient,
  requestIds: string[],
): Record<string, FeedbackRating | null> {
  const uniqueRequestIds = [...new Set(requestIds)];
  const queries = useQueries({
    queries: uniqueRequestIds.map((requestId) => ({
      queryKey: feedbackQueryKey(requestId),
      queryFn: () => client.getFeedback(requestId),
    })),
  });

  return Object.fromEntries(
    uniqueRequestIds.map((requestId, index) => [
      requestId,
      queries[index]?.data?.rating ?? null,
    ]),
  );
}

export function useSubmitFeedback(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: FeedbackCreate) => client.submitFeedback(payload),
    onSuccess: (feedback) => {
      queryClient.setQueryData(
        feedbackQueryKey(feedback.request_id),
        feedback,
      );
    },
  });
}

export function useDeleteFeedback(client: ApiClient) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (requestId: string) => client.deleteFeedback(requestId),
    onSuccess: (_data, requestId) => {
      queryClient.setQueryData(feedbackQueryKey(requestId), null);
    },
  });
}
