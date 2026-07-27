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
  canChat?: boolean;
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
        canChat={options?.canChat}
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

  it("显示免责声明且 Shift+Enter 不发送问题", () => {
    const ask = vi.spyOn(ApiClient.prototype, "askQuestionStream");
    renderWithProviders({
      canChat: true,
      currentConversation: {
        id: "conversation-new",
        title: null,
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });
    const input = screen.getByRole("textbox", { name: "问题输入" });
    fireEvent.change(input, { target: { value: "第一行\n第二行" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(screen.getByText("AI 可能出错，请核查重要信息")).toBeInTheDocument();
    expect(input).toHaveValue("第一行\n第二行");
    expect(ask).not.toHaveBeenCalled();
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

  it("在本页新会话中发送问题并流式呈现完成的 Turn", async () => {
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("基于证据");
        handlers.onToken("得出的回答");
        handlers.onDone({
          citations: [],
          request_id: "req-1",
          elapsed_ms: 18,
          conversation_id: "conversation-new",
        });
      },
    );
    renderWithProviders({
      canChat: true,
      currentConversation: {
        id: "conversation-new",
        title: null,
        document_ids: ["doc-1"],
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });
    const input = screen.getByRole("textbox", { name: "问题输入" });

    fireEvent.change(input, { target: { value: "论文结论是什么？" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("论文结论是什么？")).toBeInTheDocument();
    expect(await screen.findByText("基于证据得出的回答")).toBeInTheDocument();
    expect(ApiClient.prototype.askQuestionStream).toHaveBeenCalledWith(
      {
        question: "论文结论是什么？",
        conversation_id: "conversation-new",
      },
      expect.any(Object),
      expect.any(AbortSignal),
    );
    expect(input).toHaveValue("");
  });

  it("停止生成会中止请求、移除未完成 Turn 并还原问题", async () => {
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, _handlers, signal) =>
        new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );
    renderWithProviders({
      canChat: true,
      currentConversation: {
        id: "conversation-new",
        title: null,
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });
    const input = screen.getByRole("textbox", { name: "问题输入" });
    fireEvent.change(input, { target: { value: "请详细解释方法" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("请详细解释方法")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    expect(await screen.findByDisplayValue("请详细解释方法")).toBeEnabled();
    expect(document.querySelector(".msg.user")).toBeNull();
  });

  it("流式失败会回滚 Turn、还原问题并显示可关闭的友好错误", async () => {
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockRejectedValue(
      new TypeError("socket leaked provider details"),
    );
    renderWithProviders({
      canChat: true,
      currentConversation: {
        id: "conversation-new",
        title: null,
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });
    const input = screen.getByRole("textbox", { name: "问题输入" });
    fireEvent.change(input, { target: { value: "比较两种方法" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "生成回答失败：无法连接服务，请检查网络后重试。",
    );
    expect(screen.queryByText(/socket leaked/)).not.toBeInTheDocument();
    expect(input).toHaveValue("比较两种方法");
    expect(document.querySelector(".msg.user")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "关闭错误" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("完成回答后呈现真实引用编号并在复制成功时提示", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("结论由实验支持 [C3]。");
        handlers.onDone({
          citations: [
            {
              document_id: "doc-1",
              document_name: "paper.pdf",
              start_page: 3,
              end_page: 3,
              chunk_index: 8,
              snippet: "实验结果显示准确率提升。",
              score: 0.91,
            },
          ],
          request_id: "req-3",
          elapsed_ms: 20,
          conversation_id: "conversation-new",
          message_id: "message-3",
        });
      },
    );
    renderWithProviders({
      canChat: true,
      currentConversation: {
        id: "conversation-new",
        title: null,
        document_ids: ["doc-1"],
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    });
    const input = screen.getByRole("textbox", { name: "问题输入" });
    fireEvent.change(input, { target: { value: "结论？" } });
    fireEvent.keyDown(input, { key: "Enter" });

    const copyButton = await screen.findByRole("button", {
      name: "复制回答",
    });
    expect(
      screen.getByRole("button", { name: "查看引用 C3" }),
    ).toBeInTheDocument();
    fireEvent.click(copyButton);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "回答与来源已复制",
    );
  });

  it("切换会话会中止请求并丢弃未完成 Turn", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, _handlers, signal) => {
        requestSignal = signal;
        return new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      },
    );
    const client = new ApiClient({ apiKey: null });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const conversation = (id: string): ConversationRead => ({
      id,
      title: null,
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <ChatArea
          client={client}
          currentConversation={conversation("conversation-1")}
          canChat
        />
      </QueryClientProvider>,
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    fireEvent.change(input, { target: { value: "未完成的问题" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("未完成的问题");

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <ChatArea
          client={client}
          currentConversation={conversation("conversation-2")}
          canChat
        />
      </QueryClientProvider>,
    );

    await vi.waitFor(() => expect(requestSignal?.aborted).toBe(true));
    expect(document.querySelector(".msg.user")).toBeNull();
    expect(screen.getByRole("textbox", { name: "问题输入" })).toHaveValue("");
  });
});
