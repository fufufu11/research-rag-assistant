import { afterEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";
import { AppProvider, useApp } from "../../store/AppContext";
import { ApiClient } from "../../api/client";

function DocumentSelectionProbe() {
  const { currentDocumentIds } = useApp();
  return <output data-testid="selected-document-ids">{currentDocumentIds.join(",")}</output>;
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
});
