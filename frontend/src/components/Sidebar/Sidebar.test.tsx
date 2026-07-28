import { afterEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";
import { AppProvider, useApp } from "../../store/AppContext";
import { ApiClient, ApiClientError } from "../../api/client";

function DocumentSelectionProbe() {
  const { currentConversationId, currentDocumentIds } = useApp();
  return (
    <>
      <output data-testid="current-conversation-id">
        {currentConversationId ?? ""}
      </output>
      <output data-testid="selected-document-ids">
        {currentDocumentIds.join(",")}
      </output>
    </>
  );
}

// Sidebar 测试：用 QueryClientProvider 包裹以支持内部 useQuery hook
function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <Sidebar />
        <DocumentSelectionProbe />
      </AppProvider>
    </QueryClientProvider>,
  );
}

describe("Sidebar", () => {
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
    const header = screen.getByText(/research/);
    expect(header).toBeInTheDocument();
    const logoDot = document.querySelector(".sidebar-header .logo-dot");
    expect(logoDot).not.toBeNull();
  });

  it("渲染新建对话按钮与搜索输入框", () => {
    renderWithProviders();
    expect(screen.getByTestId("new-chat-btn")).toBeInTheDocument();
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("搜索会话…")).toBeInTheDocument();
  });

  it("渲染历史会话分组占位（空状态）", () => {
    renderWithProviders();
    expect(screen.getByTestId("nav-history")).toBeInTheDocument();
    expect(screen.getByText("历史会话")).toBeInTheDocument();
    // 空状态：query 未触发或返回空时显示「暂无会话」
    expect(screen.getByText("暂无会话")).toBeInTheDocument();
  });

  it("渲染文档库分组占位（空状态）", () => {
    renderWithProviders();
    expect(screen.getByTestId("nav-documents")).toBeInTheDocument();
    expect(screen.getByText("文档库")).toBeInTheDocument();
    expect(screen.getByText("暂无文档")).toBeInTheDocument();
  });

  it("渲染下层设置 + 帮助按钮", () => {
    renderWithProviders();
    expect(screen.getByTestId("sidebar-footer")).toBeInTheDocument();
    expect(screen.getByTestId("footer-settings")).toBeInTheDocument();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByTestId("footer-help")).toBeInTheDocument();
    expect(screen.getByText("帮助")).toBeInTheDocument();
  });

  it("可选择 READY 文档作为新对话的问答范围", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-ready",
          original_name: "rag-paper.pdf",
          stored_name: "rag-paper.pdf",
          sha256: "abc",
          page_count: 1,
          status: "ready",
          error_message: null,
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
      ],
    });

    renderWithProviders();
    const checkbox = await screen.findByRole("checkbox", {
      name: "选择文档 rag-paper.pdf",
    });
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(screen.getByTestId("selected-document-ids")).toHaveTextContent(
        "doc-ready",
      );
    });
    expect(checkbox).toBeChecked();
  });

  it("点击新建对话立即创建并锁定当前选中文档", async () => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-ready",
          original_name: "rag-paper.pdf",
          stored_name: "rag-paper.pdf",
          sha256: "abc",
          page_count: 3,
          status: "ready",
          error_message: null,
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
      ],
    });
    const createConversation = vi
      .spyOn(ApiClient.prototype, "createConversation")
      .mockResolvedValue({
        id: "conv-new",
        title: null,
        document_ids: ["doc-ready"],
        created_at: "2026-07-26T00:00:00Z",
        updated_at: "2026-07-26T00:00:00Z",
        messages: null,
      });

    renderWithProviders();
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "选择文档 rag-paper.pdf" }),
    );
    fireEvent.click(screen.getByTestId("new-chat-btn"));

    await waitFor(() => {
      expect(createConversation).toHaveBeenCalledWith({
        document_ids: ["doc-ready"],
      });
      expect(screen.getByTestId("current-conversation-id")).toHaveTextContent(
        "conv-new",
      );
    });
    expect(screen.getByTestId("selected-document-ids")).toHaveTextContent(
      "doc-ready",
    );
  });

  it("显示会话范围与时间，以及文档页数、状态和失败原因", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conv-1",
          title: "论文讨论",
          document_ids: ["doc-1", "doc-2"],
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
          messages: null,
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-1",
          original_name: "ready.pdf",
          stored_name: "ready.pdf",
          sha256: "ready",
          page_count: 12,
          status: "ready",
          error_message: null,
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
        {
          id: "doc-2",
          original_name: "broken.pdf",
          stored_name: "broken.pdf",
          sha256: "broken",
          page_count: 0,
          status: "failed",
          error_message: "PDF 已损坏",
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
      ],
    });

    renderWithProviders();

    expect(await screen.findByText("论文讨论")).toBeInTheDocument();
    expect(screen.getByText(/2 篇文档/)).toBeInTheDocument();
    expect(screen.getByText(/2026/)).toBeInTheDocument();
    expect(await screen.findByText(/12 页 · 就绪/)).toBeInTheDocument();
    expect(screen.getByText(/0 页 · 失败/)).toBeInTheDocument();
    expect(screen.getByText("PDF 已损坏")).toBeInTheDocument();
  });

  it("删除会话时不显示二次确认", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conv-1",
          title: "待删除会话",
          document_ids: [],
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
          messages: null,
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [],
    });
    const deleteConversation = vi
      .spyOn(ApiClient.prototype, "deleteConversation")
      .mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    renderWithProviders();
    fireEvent.click(await screen.findByRole("button", { name: "删除会话" }));

    await waitFor(() => {
      expect(deleteConversation).toHaveBeenCalledWith("conv-1");
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("删除文档时不显示二次确认", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-1",
          original_name: "待删除.pdf",
          stored_name: "pending-delete.pdf",
          sha256: "delete-me",
          page_count: 2,
          status: "ready",
          error_message: null,
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
      ],
    });
    const deleteDocument = vi
      .spyOn(ApiClient.prototype, "deleteDocument")
      .mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    renderWithProviders();
    fireEvent.click(await screen.findByRole("button", { name: "删除文档" }));

    await waitFor(() => {
      expect(deleteDocument).toHaveBeenCalledWith("doc-1");
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("删除不存在的文档时显示友好错误", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [
        {
          id: "doc-missing",
          original_name: "stale.pdf",
          stored_name: "stale.pdf",
          sha256: "stale",
          page_count: 2,
          status: "ready",
          error_message: null,
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "deleteDocument").mockRejectedValue(
      new ApiClientError(404, "document not found"),
    );

    renderWithProviders();
    fireEvent.click(await screen.findByRole("button", { name: "删除文档" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "删除文档失败：目标不存在或已被删除。",
    );
  });

  it("列表服务异常时显示友好错误状态", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockRejectedValue(
      new ApiClientError(500, "database unavailable"),
    );
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [],
    });

    renderWithProviders();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载会话失败：服务暂时不可用，请稍后重试。");
    expect(alert).not.toHaveTextContent("API error");
  });
});
