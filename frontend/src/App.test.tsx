import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ApiClient } from "./api/client";
import { App } from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染根布局容器（class=app）", () => {
    const { container } = render(<App />);
    const root = container.querySelector(".app");
    expect(root).not.toBeNull();
    expect(screen.getByTestId("app-root")).toBeInTheDocument();
  });

  it("同时渲染左侧栏与右侧主聊天区", () => {
    render(<App />);
    expect(screen.getByTestId("sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("chat-area")).toBeInTheDocument();
  });

  it("渲染品牌标题（科研文献智能问答）于内容占位区", () => {
    render(<App />);
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
  });

  it("新建会话后自动选中并在主区显示锁定范围", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-1",
          original_name: "paper.pdf",
          stored_name: "doc-1.pdf",
          sha256: "abc123",
          page_count: 3,
          status: "ready",
          error_message: null,
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "createConversation").mockResolvedValue({
      id: "conversation-new",
      title: null,
      document_ids: ["doc-1"],
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("checkbox", { name: "选择文档 paper.pdf" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "新建对话" }));

    expect(await screen.findByText("新会话 · 1 篇文档")).toBeInTheDocument();
  });

  it("删除当前会话后主区恢复未选择状态", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conversation-1",
          title: "论文讨论",
          document_ids: null,
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
          messages: null,
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "deleteConversation").mockResolvedValue();
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "论文讨论" }),
    );
    expect(screen.getByText("论文讨论 · 全部文档")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "删除会话 论文讨论" }),
    );

    expect(await screen.findByText("未选择会话")).toBeInTheDocument();
  });

  it("切换现有会话不覆盖下一次新建会话的文档选择", async () => {
    const document = (id: string) => ({
      id,
      original_name: `${id}.pdf`,
      stored_name: `${id}.pdf`,
      sha256: id,
      page_count: 1,
      status: "ready" as const,
      error_message: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
    });
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [document("doc-for-next"), document("doc-locked-before")],
    });
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conversation-1",
          title: "旧会话",
          document_ids: ["doc-locked-before"],
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
          messages: null,
        },
      ],
    });
    render(<App />);

    const nextDocument = await screen.findByRole("checkbox", {
      name: "选择文档 doc-for-next.pdf",
    });
    const previousDocument = screen.getByRole("checkbox", {
      name: "选择文档 doc-locked-before.pdf",
    });
    fireEvent.click(nextDocument);

    fireEvent.click(screen.getByRole("button", { name: "旧会话" }));

    expect(nextDocument).toBeChecked();
    expect(previousDocument).not.toBeChecked();
    expect(screen.getByText("旧会话 · 1 篇文档")).toBeInTheDocument();
  });
});
