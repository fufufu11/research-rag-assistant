import type { ConversationRead } from "../api/types";

type ConversationDisplay = Pick<ConversationRead, "title" | "document_ids">;

export function conversationTitle(conversation: ConversationDisplay): string {
  return conversation.title || "新会话";
}

export function conversationScopeLabel(
  conversation: ConversationDisplay,
): string {
  return conversation.document_ids?.length
    ? `${conversation.document_ids.length} 篇文档`
    : "全部文档";
}
