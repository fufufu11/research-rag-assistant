import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiClientError } from "../../api/client";
import type { ConversationRead, DocumentRead } from "../../api/types";
import { Sidebar } from "../Sidebar/Sidebar";
import { ChatArea } from "./ChatArea";

const uploadedDocument: DocumentRead = {
  id: "uploaded-doc",
  original_name: "paper.pdf",
  stored_name: "uploaded-doc.pdf",
  sha256: "abc123",
  page_count: 3,
  status: "pending",
  error_message: null,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
};

function renderWithProviders(options?: {
  includeSidebar?: boolean;
  currentConversation?: ConversationRead | null;
}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const client = new ApiClient({ apiKey: null });
  return render(
    <QueryClientProvider client={queryClient}>
      {options?.includeSidebar && <Sidebar client={client} />}
      <ChatArea
        client={client}
        currentConversation={options?.currentConversation}
      />
    </QueryClientProvider>,
  );
}

describe("ChatArea", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染主聊天区根元素", () => {
    const { container } = renderWithProviders();
    expect(container.querySelector(".chat-area")).toHaveClass("chat-area");
  });

  it("渲染顶部栏与未选择会话提示", () => {
    renderWithProviders();
    expect(screen.getByTestId("top-bar")).toBeInTheDocument();
    expect(screen.getByTestId("model-dropdown")).toBeInTheDocument();
    expect(screen.getByText("未选择会话")).toBeInTheDocument();
  });

  it("长会话摘要保留完整悬浮提示", () => {
    const title = "一篇标题非常长且需要在窄屏顶部栏中安全截断的科研论文讨论";
    renderWithProviders({
      currentConversation: {
        id: "conversation-long-title",
        title,
        document_ids: ["doc-1", "doc-2"],
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });

    expect(screen.getByTitle(`${title} · 2 篇文档`)).toBeInTheDocument();
  });

  it("渲染居中内容占位", () => {
    renderWithProviders();
    expect(screen.getByTestId("content-placeholder")).toBeInTheDocument();
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
    expect(screen.getByText("从左侧选择或新建对话开始")).toBeInTheDocument();
  });

  it("底部上传入口使用原生单文件 PDF 选择器", () => {
    renderWithProviders();
    const input = screen.getByLabelText("选择 PDF 文档");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("accept", "application/pdf,.pdf");
    expect(input).not.toHaveAttribute("multiple");
    expect(screen.getByRole("button", { name: "上传 PDF" })).toBeInTheDocument();
  });

  it("上传成功后刷新共享文档列表", async () => {
    const listDocuments = vi
      .spyOn(ApiClient.prototype, "listDocuments")
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: [uploadedDocument] });
    const uploadDocument = vi
      .spyOn(ApiClient.prototype, "uploadDocument")
      .mockResolvedValue(uploadedDocument);
    renderWithProviders({ includeSidebar: true });
    await screen.findByText("暂无文档");
    const file = new File(["pdf"], "paper.pdf", {
      type: "application/pdf",
    });

    fireEvent.change(screen.getByLabelText("选择 PDF 文档"), {
      target: { files: [file] },
    });

    expect(await screen.findByText("已上传 paper.pdf")).toBeInTheDocument();
    expect(await screen.findByText("paper.pdf")).toBeInTheDocument();
    expect(uploadDocument).toHaveBeenCalledWith(file);
    expect(listDocuments.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("上传期间禁用入口并显示明确状态", async () => {
    let finishUpload: ((value: DocumentRead) => void) | undefined;
    vi.spyOn(ApiClient.prototype, "uploadDocument").mockImplementation(
      () =>
        new Promise<DocumentRead>((resolve) => {
          finishUpload = resolve;
        }),
    );
    renderWithProviders();
    const file = new File(["pdf"], "paper.pdf", {
      type: "application/pdf",
    });

    fireEvent.change(screen.getByLabelText("选择 PDF 文档"), {
      target: { files: [file] },
    });

    expect(await screen.findByText("正在上传 paper.pdf…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在上传 PDF" })).toBeDisabled();
    await act(async () => finishUpload?.(uploadedDocument));
  });

  it("拒绝非 PDF 文件且不调用上传接口", async () => {
    const uploadDocument = vi.spyOn(ApiClient.prototype, "uploadDocument");
    renderWithProviders();

    fireEvent.change(screen.getByLabelText("选择 PDF 文档"), {
      target: {
        files: [new File(["text"], "notes.txt", { type: "text/plain" })],
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "上传文档失败：只支持单个 PDF 文件。",
    );
    expect(uploadDocument).not.toHaveBeenCalled();
  });

  it("上传 400 错误显示友好提示且不暴露后端详情", async () => {
    vi.spyOn(ApiClient.prototype, "uploadDocument").mockRejectedValue(
      new ApiClientError(400, "PDF 文件为空"),
    );
    renderWithProviders();
    const file = new File(["pdf"], "paper.pdf", {
      type: "application/pdf",
    });

    fireEvent.change(screen.getByLabelText("选择 PDF 文档"), {
      target: { files: [file] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "上传文档失败：请求内容有误，请检查后重试。",
    );
    expect(screen.queryByText(/PDF 文件为空/)).not.toBeInTheDocument();
    expect(screen.queryByText(/API error/)).not.toBeInTheDocument();
  });

  it("上传控件不会改变内容占位区的稳定布局类", () => {
    const { container } = renderWithProviders();
    expect(container.querySelector(".content-placeholder")).toHaveClass(
      "content-placeholder",
    );
    expect(screen.getByTestId("input-bar")).toBeInTheDocument();
  });
});
