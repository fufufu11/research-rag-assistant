import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiClient, ApiClientError } from "../../api/client";
import type {
  ConversationRead,
  DocumentRead,
  DocumentStatus,
} from "../../api/types";
import { Sidebar } from "./Sidebar";

function makeDocument(
  id: string,
  status: DocumentStatus,
  options: Partial<DocumentRead> = {},
): DocumentRead {
  return {
    id,
    original_name: `${id}.pdf`,
    stored_name: `${id}.pdf`,
    sha256: id,
    page_count: 1,
    status,
    error_message: null,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
    ...options,
  };
}

function makeConversation(
  id: string,
  options: Partial<ConversationRead> = {},
): ConversationRead {
  return {
    id,
    title: id,
    document_ids: null,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
    messages: null,
    ...options,
  };
}

function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const client = new ApiClient({ apiKey: null });

  function SidebarHarness() {
    const [currentConversationId, setCurrentConversationId] = useState<
      string | null
    >(null);
    const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
    return (
      <Sidebar
        client={client}
        currentConversationId={currentConversationId}
        selectedDocumentIds={selectedDocumentIds}
        onSelectedDocumentIdsChange={setSelectedDocumentIds}
        onSelectConversation={(conversation) =>
          setCurrentConversationId(conversation.id)
        }
      />
    );
  }

  return render(
    <QueryClientProvider client={queryClient}>
      <SidebarHarness />
    </QueryClientProvider>,
  );
}

describe("Sidebar", () => {
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

  it("渲染深棕色背景的侧栏根元素", () => {
    const { container } = renderWithProviders();
    const sidebar = container.querySelector(".sidebar");
    expect(sidebar).not.toBeNull();
    expect(sidebar?.className).toContain("sidebar");
  });

  it("渲染品牌 header（logo dot + research·rag）", () => {
    renderWithProviders();
    expect(screen.getByText(/research/)).toBeInTheDocument();
    expect(document.querySelector(".sidebar-header .logo-dot")).not.toBeNull();
  });

  it("保留新建对话按钮与搜索输入框的 T2 布局", () => {
    renderWithProviders();
    expect(screen.getByTestId("new-chat-btn")).toBeInTheDocument();
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("搜索会话…")).toBeInTheDocument();
  });

  it("渲染历史会话分组占位", async () => {
    renderWithProviders();
    expect(screen.getByTestId("nav-history")).toBeInTheDocument();
    expect(screen.getByText("历史会话")).toBeInTheDocument();
    expect(await screen.findByText("暂无会话")).toBeInTheDocument();
  });

  it("会话列表请求未完成时显示加载状态", () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockReturnValue(
      new Promise(() => undefined),
    );
    renderWithProviders();

    expect(screen.getByText("正在加载会话…")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.queryByText("暂无会话")).not.toBeInTheDocument();
  });

  it("会话列表 500 错误显示友好提示", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockRejectedValue(
      new ApiClientError(500, "database unavailable"),
    );
    renderWithProviders();

    expect(await screen.findByText("加载会话失败：服务暂时不可用，请稍后重试。"))
      .toHaveAttribute("role", "alert");
    expect(screen.queryByText(/database unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText("暂无会话")).not.toBeInTheDocument();
  });

  it("显示会话标题、创建日期与锁定文档数量", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        makeConversation("conversation-1", {
          title: "论文讨论",
          document_ids: ["doc-1", "doc-2"],
        }),
        makeConversation("conversation-2", { title: null }),
      ],
    });
    renderWithProviders();

    expect(await screen.findByText("论文讨论")).toBeInTheDocument();
    expect(screen.getByText("新会话")).toBeInTheDocument();
    expect(screen.getAllByText("2026/07/27")).toHaveLength(2);
    expect(screen.getByText("2 篇文档")).toBeInTheDocument();
    expect(screen.getByText("全部文档")).toBeInTheDocument();
  });

  it("历史会话区默认展开并可折叠", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [makeConversation("conversation-1", { title: "论文讨论" })],
    });
    renderWithProviders();

    const toggle = screen.getByRole("button", { name: "历史会话" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("论文讨论")).toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("论文讨论")).not.toBeInTheDocument();
  });

  it("点击会话后将其切换为当前会话并高亮", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [makeConversation("conversation-1", { title: "论文讨论" })],
    });
    renderWithProviders();

    const conversation = await screen.findByRole("button", {
      name: "论文讨论",
    });
    expect(conversation).not.toHaveAttribute("aria-current");

    fireEvent.click(conversation);

    expect(conversation).toHaveAttribute("aria-current", "page");
  });

  it("文档区默认展开并可折叠", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [makeDocument("paper", "ready")],
    });
    renderWithProviders();

    const toggle = screen.getByRole("button", { name: "文档库" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("paper.pdf")).toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("paper.pdf")).not.toBeInTheDocument();
  });

  it("只允许选择就绪文档作为新会话范围", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        makeDocument("ready-paper", "ready"),
        makeDocument("indexing-paper", "processing"),
      ],
    });
    renderWithProviders();

    const ready = await screen.findByRole("checkbox", {
      name: "选择文档 ready-paper.pdf",
    });
    const indexing = screen.getByRole("checkbox", {
      name: "选择文档 indexing-paper.pdf",
    });
    expect(ready).toBeEnabled();
    expect(indexing).toBeDisabled();

    fireEvent.click(ready);

    expect(ready).toBeChecked();
  });

  it("新建对话提交选中的文档范围", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [makeDocument("ready-paper", "ready")],
    });
    const createConversation = vi
      .spyOn(ApiClient.prototype, "createConversation")
      .mockResolvedValue(
        makeConversation("conversation-new", {
          title: null,
          document_ids: ["ready-paper"],
        }),
      );
    renderWithProviders();

    fireEvent.click(
      await screen.findByRole("checkbox", {
        name: "选择文档 ready-paper.pdf",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "新建对话" }));

    await waitFor(() => {
      expect(createConversation).toHaveBeenCalledWith({
        document_ids: ["ready-paper"],
      });
    });
  });

  it("删除会话不显示二次确认并在成功后刷新列表", async () => {
    const conversation = makeConversation("conversation-1", {
      title: "论文讨论",
    });
    vi.spyOn(ApiClient.prototype, "listConversations")
      .mockResolvedValueOnce({ items: [conversation] })
      .mockResolvedValue({ items: [] });
    const deleteConversation = vi
      .spyOn(ApiClient.prototype, "deleteConversation")
      .mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders();

    fireEvent.click(
      await screen.findByRole("button", { name: "删除会话 论文讨论" }),
    );

    await waitFor(() => {
      expect(deleteConversation).toHaveBeenCalledWith("conversation-1");
      expect(screen.queryByText("论文讨论")).not.toBeInTheDocument();
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("文档区折叠时仍显示创建会话的友好错误", async () => {
    vi.spyOn(ApiClient.prototype, "createConversation").mockRejectedValue(
      new ApiClientError(500, "database unavailable"),
    );
    renderWithProviders();
    fireEvent.click(screen.getByRole("button", { name: "文档库" }));

    fireEvent.click(screen.getByRole("button", { name: "新建对话" }));

    expect(
      await screen.findByText("创建会话失败：服务暂时不可用，请稍后重试。"),
    ).toHaveAttribute("role", "alert");
  });

  it.each([
    {
      status: 422,
      detail: [{ loc: ["body", "document_ids"], msg: "invalid value" }],
      rawText: "invalid value",
    },
    {
      status: 409,
      detail: "database constraint failed",
      rawText: "database constraint failed",
    },
  ])(
    "创建会话 $status 错误不暴露原始详情",
    async ({ status, detail, rawText }) => {
      vi.spyOn(ApiClient.prototype, "createConversation").mockRejectedValue(
        new ApiClientError(status, detail),
      );
      renderWithProviders();

      fireEvent.click(screen.getByRole("button", { name: "新建对话" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        status === 422
          ? "创建会话失败：请求内容有误，请检查后重试。"
          : "创建会话失败：请求未能完成，请重试。",
      );
      expect(screen.queryByText(new RegExp(rawText))).not.toBeInTheDocument();
    },
  );

  it("文档区折叠时仍显示删除会话的友好错误", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [makeConversation("conversation-1", { title: "论文讨论" })],
    });
    vi.spyOn(ApiClient.prototype, "deleteConversation").mockRejectedValue(
      new ApiClientError(404, "conversation not found"),
    );
    renderWithProviders();
    fireEvent.click(screen.getByRole("button", { name: "文档库" }));

    fireEvent.click(
      await screen.findByRole("button", { name: "删除会话 论文讨论" }),
    );

    expect(
      await screen.findByText("删除会话失败：目标不存在或已被删除。"),
    ).toHaveAttribute("role", "alert");
  });

  it("显示文档名称、页数、四种状态和失败原因", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        makeDocument("queued", "pending"),
        makeDocument("indexing", "processing", { page_count: 2 }),
        makeDocument("ready", "ready", { page_count: 12 }),
        makeDocument("broken", "failed", {
          page_count: 0,
          error_message: "PDF 已损坏",
        }),
      ],
    });
    renderWithProviders();

    expect(await screen.findByText("queued.pdf")).toBeInTheDocument();
    expect(screen.getByText("1 页 · 等待处理")).toBeInTheDocument();
    expect(screen.getByText("2 页 · 处理中")).toBeInTheDocument();
    expect(screen.getByText("12 页 · 就绪")).toBeInTheDocument();
    expect(screen.getByText("0 页 · 失败")).toBeInTheDocument();
    expect(screen.getByText("PDF 已损坏")).toBeInTheDocument();
  });

  it("删除文档不显示二次确认并在成功后刷新列表", async () => {
    const document = makeDocument("paper", "ready");
    const listDocuments = vi
      .spyOn(ApiClient.prototype, "listDocuments")
      .mockResolvedValueOnce({ items: [document] })
      .mockResolvedValue({ items: [] });
    const deleteDocument = vi
      .spyOn(ApiClient.prototype, "deleteDocument")
      .mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders();

    fireEvent.click(
      await screen.findByRole("button", { name: "删除文档 paper.pdf" }),
    );

    await waitFor(() => {
      expect(deleteDocument).toHaveBeenCalledWith("paper");
      expect(listDocuments.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(screen.queryByText("paper.pdf")).not.toBeInTheDocument();
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("文档列表 500 错误显示友好提示", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockRejectedValue(
      new ApiClientError(500, "database unavailable"),
    );
    renderWithProviders();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "加载文档失败：服务暂时不可用，请稍后重试。",
    );
    expect(screen.queryByText(/API error/)).not.toBeInTheDocument();
  });

  it("渲染下层设置与帮助按钮", () => {
    renderWithProviders();
    expect(screen.getByTestId("sidebar-footer")).toBeInTheDocument();
    expect(screen.getByTestId("footer-settings")).toBeInTheDocument();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByTestId("footer-help")).toBeInTheDocument();
    expect(screen.getByText("帮助")).toBeInTheDocument();
  });
});
