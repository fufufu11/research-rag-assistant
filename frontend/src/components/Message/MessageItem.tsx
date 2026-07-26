import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { MessageRead, CitationRead, FeedbackRating } from "../../api/types";
import { CitationCard } from "./CitationCard";

// MessageItem：单条消息渲染
// - user：右对齐深棕气泡
// - assistant：左对齐纯段落（Newsreader 衬线），含 markdown 渲染
// - assistant 消息底部含引用卡片网格 + 复制按钮 + 反馈按钮（T7）
// - 流式输出时 isStreaming=true 显示光标
interface MessageItemProps {
  message: MessageRead;
  isStreaming?: boolean;
  // 反馈相关（T7）
  feedbackRating?: FeedbackRating | null;
  onFeedback?: (rating: FeedbackRating) => void;
  onCopy?: (text: string) => void;
}

export function MessageItem({
  message,
  isStreaming = false,
  feedbackRating,
  onFeedback,
  onCopy,
}: MessageItemProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      onCopy?.(message.content);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard API 在非 https / 非localhost 不可用，静默失败
    }
  };

  const handleFeedback = (rating: FeedbackRating) => {
    // 切换：再次点击当前激活的 rating 视为取消
    if (feedbackRating === rating) {
      onFeedback?.(rating); // 由上层决定是否调 deleteFeedback
    } else {
      onFeedback?.(rating);
    }
  };

  const citations: CitationRead[] = message.citations ?? [];

  return (
    <div className={`msg ${message.role}`} data-testid={`msg-${message.role}`}>
      <div className="msg-avatar" aria-hidden="true">
        {isUser ? "你" : "AI"}
      </div>
      <div className="msg-content">
        <div className="msg-author">{isUser ? "USER" : "ASSISTANT"}</div>
        {isUser ? (
          <div className="msg-body">{message.content}</div>
        ) : (
          <div className="msg-body" data-testid="assistant-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || (isStreaming ? "" : "")}
            </ReactMarkdown>
            {isStreaming && (
              <span className="stream-cursor" aria-label="生成中" />
            )}
          </div>
        )}

        {/* 引用卡片（仅 assistant 有 citations 时） */}
        {!isUser && citations.length > 0 && (
          <div className="citations" data-testid="citations">
            {citations.map((cite, i) => (
              <CitationCard
                key={`${cite.document_id}-${cite.chunk_index}-${i}`}
                citation={cite}
                index={i + 1}
              />
            ))}
          </div>
        )}

        {/* 消息底部操作（仅 assistant 完成后显示） */}
        {!isUser && !isStreaming && (
          <div className="msg-actions" data-testid="msg-actions">
            <button
              type="button"
              className={`msg-action-btn ${copied ? "active" : ""}`}
              onClick={handleCopy}
              aria-label="复制回答"
            >
              <span aria-hidden="true">{copied ? "✓" : "⧉"}</span>
              <span>{copied ? "已复制" : "复制"}</span>
            </button>
            {onFeedback && (
              <>
                <button
                  type="button"
                  className={`msg-action-btn ${
                    feedbackRating === "like" ? "active" : ""
                  }`}
                  onClick={() => handleFeedback("like")}
                  aria-label="点赞"
                  aria-pressed={feedbackRating === "like"}
                >
                  <span aria-hidden="true">▲</span>
                  <span>赞</span>
                </button>
                <button
                  type="button"
                  className={`msg-action-btn dislike ${
                    feedbackRating === "dislike" ? "active" : ""
                  }`}
                  onClick={() => handleFeedback("dislike")}
                  aria-label="点踩"
                  aria-pressed={feedbackRating === "dislike"}
                >
                  <span aria-hidden="true">▼</span>
                  <span>踩</span>
                </button>
              </>
            )}
            {message.request_id && (
              <div className="msg-meta" title="request_id">
                req {(message.request_id ?? "").slice(0, 8)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
