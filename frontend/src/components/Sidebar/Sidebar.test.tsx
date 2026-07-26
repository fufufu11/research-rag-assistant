import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiClient, ApiClientError } from "../../api/client";
import type { DocumentRead, DocumentStatus } from "../../api/types";
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

function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const client = new ApiClient({ apiKey: null });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <Sidebar client={client} />
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

describe("Sidebar", () => {
  beforeEach(() => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
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

  it("渲染历史会话分组占位", () => {
    renderWithProviders();
    expect(screen.getByTestId("nav-history")).toBeInTheDocument();
    expect(screen.getByText("历史会话")).toBeInTheDocument();
    expect(screen.getByText("暂无会话")).toBeInTheDocument();
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
