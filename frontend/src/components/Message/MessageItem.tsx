import { useId, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { MessageRead } from "../../api/types";
import { CitationCard } from "./CitationCard";
import { formatCitationPages } from "./citation";

interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

function remarkCitationLinks() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!node.children || node.type === "code" || node.type === "inlineCode") {
        return;
      }
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          visit(child);
          return [child];
        }
        const parts: MarkdownNode[] = [];
        const pattern = /\[C(\d+)\]/gi;
        let cursor = 0;
        for (const match of child.value.matchAll(pattern)) {
          const index = match.index ?? 0;
          if (index > cursor) {
            parts.push({ type: "text", value: child.value.slice(cursor, index) });
          }
          const label = `C${match[1]}`;
          parts.push({
            type: "link",
            url: `#citation-${label}`,
            children: [{ type: "text", value: `[${label}]` }],
          });
          cursor = index + match[0].length;
        }
        if (cursor === 0) return [child];
        if (cursor < child.value.length) {
          parts.push({ type: "text", value: child.value.slice(cursor) });
        }
        return parts;
      });
    };
    visit(tree);
  };
}

function citationLabels(content: string): string[] {
  const labels: string[] = [];
  for (const match of content.matchAll(/\[C(\d+)\]/gi)) {
    const label = `C${match[1]}`;
    if (!labels.includes(label)) labels.push(label);
  }
  return labels;
}

interface MessageItemProps {
  message: MessageRead;
  isStreaming?: boolean;
  onCopied?: () => void;
}

export function MessageItem({
  message,
  isStreaming = false,
  onCopied,
}: MessageItemProps) {
  const answerRef = useRef<HTMLDivElement>(null);
  const citationIdPrefix = useId();
  const [copied, setCopied] = useState(false);
  const labels = citationLabels(message.content);
  const citations = (message.citations ?? []).flatMap((citation, index) =>
    labels[index] ? [{ citation, label: labels[index] }] : [],
  );
  const citationTargetId = (label: string) =>
    `${citationIdPrefix}-citation-${label}`;

  const showCitation = (label: string) => {
    const card = document.getElementById(citationTargetId(label));
    if (!card) return;
    card.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    card.focus({ preventScroll: true });
    card.classList.add("citation-highlight");
    window.setTimeout(() => card.classList.remove("citation-highlight"), 1600);
  };

  const copyAnswer = async () => {
    const answer = (answerRef.current?.innerText ?? answerRef.current?.textContent ?? "")
      .trim()
      .replace(/\n{3,}/g, "\n\n");
    const sources = citations.map(({ citation, label }) => {
      return `[${label}] ${citation.document_name}，${formatCitationPages(citation)}`;
    });
    const text = sources.length
      ? `${answer}\n\n来源：\n${sources.join("\n")}`
      : answer;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      onCopied?.();
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      return;
    }
  };

  return (
    <article className={`msg ${message.role}`} data-testid={`message-${message.role}`}>
      <div className="msg-content">
        {message.role === "assistant" ? (
          <div className="msg-body assistant-body" ref={answerRef}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkCitationLinks]}
              components={{
                a: ({ href, children }) => {
                  if (href?.startsWith("#citation-")) {
                    const label = href.slice("#citation-".length);
                    return (
                      <button
                        type="button"
                        className="citation-marker"
                        aria-label={`查看引用 ${label}`}
                        onClick={() => showCitation(label)}
                      >
                        {children}
                      </button>
                    );
                  }
                  return <a href={href}>{children}</a>;
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
            {isStreaming && <span className="stream-cursor" aria-label="生成中" />}
          </div>
        ) : (
          <div className="msg-body">{message.content}</div>
        )}
        {citations.length > 0 && (
          <div className="citations" aria-label="引用来源">
            {citations.map(({ citation, label }) => (
              <CitationCard
                citation={citation}
                label={label}
                targetId={citationTargetId(label)}
                key={`${label}-${citation.document_id}-${citation.chunk_index}`}
              />
            ))}
          </div>
        )}
        {message.role === "assistant" && !isStreaming && (
          <div className="message-actions">
            <button
              type="button"
              className="message-action"
              aria-label={copied ? "已复制回答" : "复制回答"}
              title={copied ? "已复制" : "复制回答"}
              onClick={() => void copyAnswer()}
            >
              {copied ? (
                <Check aria-hidden="true" size={16} />
              ) : (
                <Copy aria-hidden="true" size={16} />
              )}
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
