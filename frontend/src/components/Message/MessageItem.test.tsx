import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageItem } from "./MessageItem";
import type { MessageRead } from "../../api/types";

const baseAssistantMessage: MessageRead = {
  id: "msg-1",
  role: "assistant",
  content: "## 测试标题\n\n这是 **加粗** 文本。",
  citations: [
    {
      document_id: "doc-1",
      document_name: "paper1.pdf",
      start_page: 3,
      end_page: 3,
      chunk_index: 0,
      snippet: "检索到的片段",
      score: 0.85,
    },
  ],
  request_id: "req-abc123",
  created_at: "2026-07-26T10:00:00Z",
};

const baseUserMessage: MessageRead = {
  id: "msg-2",
  role: "user",
  content: "你好",
  citations: null,
  request_id: null,
  created_at: "2026-07-26T10:00:01Z",
};

describe("MessageItem", () => {
  beforeEach(() => {
    // clipboard API 在 jsdom 不存在，需提前 mock
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("user 消息右对齐深棕气泡 + USER 作者标签", () => {
    const { container } = render(<MessageItem message={baseUserMessage} />);
    const msg = container.querySelector(".msg.user");
    expect(msg).not.toBeNull();
    expect(screen.getByText("USER")).toBeInTheDocument();
    expect(screen.getByText("你好")).toBeInTheDocument();
  });

  it("assistant 消息渲染 Newsreader 衬线段落 + ASSISTANT 标签", () => {
    const { container } = render(
      <MessageItem message={baseAssistantMessage} />,
    );
    const msg = container.querySelector(".msg.assistant");
    expect(msg).not.toBeNull();
    expect(screen.getByText("ASSISTANT")).toBeInTheDocument();
  });

  it("assistant 消息渲染 markdown（h2 + 加粗 + 段落）", () => {
    render(<MessageItem message={baseAssistantMessage} />);
    expect(screen.getByText("测试标题")).toBeInTheDocument();
    expect(screen.getByText("加粗")).toBeInTheDocument();
  });

  it("assistant 消息底部渲染引用卡片网格", () => {
    render(<MessageItem message={baseAssistantMessage} />);
    expect(screen.getByTestId("citations")).toBeInTheDocument();
    expect(screen.getByTestId("cite-card-1")).toBeInTheDocument();
    expect(screen.getByText("paper1.pdf")).toBeInTheDocument();
    expect(screen.getByText(/第 3 页/)).toBeInTheDocument();
    expect(screen.getByText("score 85%")).toBeInTheDocument();
  });

  it("assistant 消息底部渲染复制按钮（点击后调用 onCopy + 切换状态）", async () => {
    const onCopy = vi.fn();
    render(<MessageItem message={baseAssistantMessage} onCopy={onCopy} />);
    const copyBtn = screen.getByLabelText("复制回答");
    expect(copyBtn).toBeInTheDocument();
    fireEvent.click(copyBtn);
    // useState 异步更新 copied，需 await
    await new Promise((r) => setTimeout(r, 0));
    expect(onCopy).toHaveBeenCalled();
  });

  it("assistant 消息底部渲染赞/踩按钮（点击触发 onFeedback）", () => {
    const onFeedback = vi.fn();
    render(
      <MessageItem
        message={baseAssistantMessage}
        onFeedback={onFeedback}
        feedbackRating={null}
      />,
    );
    const likeBtn = screen.getByLabelText("点赞");
    const dislikeBtn = screen.getByLabelText("点踩");
    fireEvent.click(likeBtn);
    expect(onFeedback).toHaveBeenCalledWith("like");
    fireEvent.click(dislikeBtn);
    expect(onFeedback).toHaveBeenCalledWith("dislike");
  });

  it("已激活的赞按钮再次点击视为取消（仍调 onFeedback）", () => {
    const onFeedback = vi.fn();
    render(
      <MessageItem
        message={baseAssistantMessage}
        onFeedback={onFeedback}
        feedbackRating="like"
      />,
    );
    const likeBtn = screen.getByLabelText("点赞");
    expect(likeBtn).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(likeBtn);
    expect(onFeedback).toHaveBeenCalledWith("like");
  });

  it("流式输出时显示 stream-cursor 且不显示操作按钮", () => {
    const { container } = render(
      <MessageItem message={baseAssistantMessage} isStreaming={true} />,
    );
    const cursor = container.querySelector(".stream-cursor");
    expect(cursor).not.toBeNull();
    expect(screen.queryByTestId("msg-actions")).toBeNull();
  });

  it("assistant 无 citations 时不渲染引用网格", () => {
    render(
      <MessageItem
        message={{ ...baseAssistantMessage, citations: null }}
      />,
    );
    expect(screen.queryByTestId("citations")).toBeNull();
  });

  it("显示 request_id 前 8 字符", () => {
    render(<MessageItem message={baseAssistantMessage} />);
    expect(screen.getByText(/req req-abc1/)).toBeInTheDocument();
  });
});
