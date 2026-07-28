import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledOnce();
    });
    const copiedText = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0];
    expect(copiedText).toContain("测试标题");
    expect(copiedText).toContain("这是 加粗 文本。");
    expect(copiedText).not.toMatch(/##|\*\*/);
    expect(onCopy).toHaveBeenCalledWith(copiedText);
  });

  it("copies rendered GFM content without markdown syntax", async () => {
    const message: MessageRead = {
      ...baseAssistantMessage,
      content: [
        "~~Removed~~",
        "",
        "| Name | Value |",
        "| --- | --- |",
        "| Alpha | 1 |",
        "",
        "- [x] Done",
        "- [ ] Todo",
      ].join("\n"),
    };
    render(<MessageItem message={message} />);

    fireEvent.click(screen.getByLabelText("复制回答"));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledOnce();
    });
    const copiedText = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0];
    expect(copiedText).toContain("Removed");
    expect(copiedText).toContain("Name");
    expect(copiedText).toContain("Value");
    expect(copiedText).toContain("Alpha");
    expect(copiedText).toContain("Done");
    expect(copiedText).toContain("Todo");
    expect(copiedText).not.toMatch(/~~|\||\[x\]|\[ \]/);
  });

  it("assistant 消息底部点赞按钮点击后立即触发 onFeedback", () => {
    const onFeedback = vi.fn();
    render(
      <MessageItem
        message={baseAssistantMessage}
        onFeedback={onFeedback}
        feedbackRating={null}
      />,
    );
    const likeBtn = screen.getByLabelText("点赞");
    fireEvent.click(likeBtn);
    expect(onFeedback).toHaveBeenCalledWith("like", undefined);
  });

  it("点踩时填写可选评论并在提交时一并反馈", () => {
    const onFeedback = vi.fn();
    render(
      <MessageItem
        message={baseAssistantMessage}
        onFeedback={onFeedback}
        feedbackRating={null}
      />,
    );

    fireEvent.click(screen.getByLabelText("点踩"));
    const comment = screen.getByLabelText("点踩原因");
    expect(comment).toHaveAttribute("maxlength", "2000");
    fireEvent.change(comment, { target: { value: "引用不够准确" } });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    expect(onFeedback).toHaveBeenCalledWith("dislike", "引用不够准确");
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
    expect(onFeedback).toHaveBeenCalledWith("like", undefined);
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
