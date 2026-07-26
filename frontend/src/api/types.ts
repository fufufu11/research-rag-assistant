// API 类型定义，严格对应后端 src/research_rag/api/schemas.py。
// 后端 schema 改动需同步此文件（ADR 0005）。

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export type FeedbackRating = "like" | "dislike";

export interface DocumentRead {
  id: string;
  original_name: string;
  stored_name: string;
  sha256: string;
  page_count: number;
  status: DocumentStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentList {
  items: DocumentRead[];
}

export interface CitationRead {
  document_id: string;
  document_name: string;
  start_page: number;
  end_page: number;
  chunk_index: number;
  snippet: string;
  score: number;
}

export interface QueryRequest {
  question: string;
  document_ids?: string[];
  top_k?: number;
  stream?: boolean;
  conversation_id?: string | null;
}

export interface QueryResponse {
  answer: string;
  citations: CitationRead[];
  request_id: string;
  elapsed_ms: number;
  conversation_id: string | null;
}

export interface ConversationCreate {
  title?: string | null;
  document_ids?: string[] | null;
}

export interface MessageRead {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: CitationRead[] | null;
  request_id: string | null;
  created_at: string;
}

export interface ConversationRead {
  id: string;
  title: string | null;
  document_ids: string[] | null;
  created_at: string;
  updated_at: string;
  messages: MessageRead[] | null;
}

export interface ConversationList {
  items: ConversationRead[];
}

export interface FeedbackCreate {
  request_id: string;
  rating: FeedbackRating;
  message_id?: string | null;
  comment?: string | null;
}

export interface FeedbackRead {
  id: string;
  request_id: string;
  message_id: string | null;
  rating: FeedbackRating;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

export interface FeedbackList {
  items: FeedbackRead[];
}

export interface ErrorResponse {
  detail: string;
}
