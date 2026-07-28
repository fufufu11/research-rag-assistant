import { afterEach, describe, it, expect, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { ChatArea } from "./ChatArea";
import { AppProvider, useApp } from "../../store/AppContext";
import { ApiClient, ApiClientError } from "../../api/client";

function DocumentSelectionProbe() {
  const { currentDocumentIds } = useApp();
  return <output data-testid="selected-document-ids">{currentDocumentIds.join(",")}</output>;
}

function SelectConversation({ id }: { id: string }) {
  const { setCurrentConversationId } = useApp();
  useEffect(() => setCurrentConversationId(id), [id, setCurrentConversationId]);
  return null;
}

function ConversationSwitcher() {
  const { setCurrentConversationId } = useApp();
  return (
    <button
      type="button"
      onClick={() => setCurrentConversationId("conversation-2")}
    >
      Switch conversation
    </button>
  );
}

function renderWithProviders(conversationId?: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        {conversationId && <SelectConversation id={conversationId} />}
        <ChatArea />
        <DocumentSelectionProbe />
        <ConversationSwitcher />
      </AppProvider>
    </QueryClientProvider>,
  );
}

describe("ChatArea", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染主聊天区根元素", () => {
    const { container } = renderWithProviders();
    const chatArea = container.querySelector(".chat-area");
    expect(chatArea).not.toBeNull();
  });

  it("渲染顶部栏（含模型下拉与未选择会话提示）", () => {
    renderWithProviders();
    expect(screen.getByTestId("top-bar")).toBeInTheDocument();
    expect(screen.getByTestId("model-dropdown")).toBeInTheDocument();
    expect(screen.getByText("未选择会话")).toBeInTheDocument();
  });

  it("渲染居中 720px 内容占位（含欢迎标题与说明）", () => {
    renderWithProviders();
    const placeholder = screen.getByTestId("content-placeholder");
    expect(placeholder).toBeInTheDocument();
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
    expect(screen.getByText("选择文档或新建对话开始")).toBeInTheDocument();
  });

  it("渲染底部输入栏（pill + 上传按钮 + 发送按钮 + 免责声明）", () => {
    renderWithProviders();
    expect(screen.getByTestId("input-wrap")).toBeInTheDocument();
    expect(screen.getByTestId("input-pill")).toBeInTheDocument();
    expect(screen.getByTestId("upload-btn")).toBeInTheDocument();
    expect(screen.getByTestId("send-btn")).toBeInTheDocument();
    expect(screen.getByText("AI 可能出错，请核实重要信息")).toBeInTheDocument();
  });

  it("上传成功后自动只选中新 PDF 作为问答范围", async () => {
    vi.spyOn(ApiClient.prototype, "uploadDocument").mockResolvedValue({
      id: "uploaded-doc",
      original_name: "uploaded.pdf",
      stored_name: "uploaded.pdf",
      sha256: "def",
      page_count: 1,
      status: "ready",
      error_message: null,
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
    });

    renderWithProviders();
    const file = new File(["pdf"], "uploaded.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(screen.getByTestId("file-input"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(screen.getByTestId("selected-document-ids")).toHaveTextContent(
        "uploaded-doc",
      );
    });
    expect(screen.getByText("已上传并选中「uploaded.pdf」")).toBeInTheDocument();
  });

  it("新会话的空历史响应不会覆盖正在生成的消息", async () => {
    const conversation = {
      id: "conversation-1",
      title: "RAG question",
      document_ids: ["doc-1"],
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      messages: [],
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    const createConversation = vi
      .spyOn(ApiClient.prototype, "createConversation")
      .mockResolvedValue(conversation);
    const getConversation = vi
      .spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValue(conversation);
    vi.spyOn(ApiClient.prototype, "getFeedback").mockResolvedValue(null);
    const submitFeedback = vi
      .spyOn(ApiClient.prototype, "submitFeedback")
      .mockResolvedValue({
        id: "feedback-stream",
        request_id: "request-1",
        message_id: "message-real",
        rating: "like",
        comment: null,
        created_at: "2026-07-26T00:00:00Z",
        updated_at: "2026-07-26T00:00:00Z",
      });

    let finishStream: (() => void) | undefined;
    const streamGate = new Promise<void>((resolve) => {
      finishStream = resolve;
    });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("partial answer");
        await streamGate;
        handlers.onDone({
          citations: [],
          request_id: "request-1",
          elapsed_ms: 20,
          conversation_id: conversation.id,
          message_id: "message-real",
        });
      },
    );

    renderWithProviders();
    const input = screen.getByTestId("input-field");
    fireEvent.change(input, { target: { value: "What is RAG?" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    expect(createConversation).toHaveBeenCalledWith({ document_ids: null });
    await waitFor(() => expect(getConversation).toHaveBeenCalled());
    expect(screen.getByText("What is RAG?")).toBeInTheDocument();

    await act(async () => {
      finishStream?.();
      await streamGate;
    });

    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    expect(screen.getByText("What is RAG?")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("点赞"));
    await waitFor(() => {
      expect(submitFeedback).toHaveBeenCalledWith({
        request_id: "request-1",
        rating: "like",
        message_id: "message-real",
        comment: undefined,
      });
    });
  });

  it("switching conversations during a stream shows the selected history", async () => {
    const conversation = (id: string, content?: string) => ({
      id,
      title: id,
      document_ids: [],
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      messages: content
        ? [
            {
              id: `${id}-message`,
              role: "assistant" as const,
              content,
              citations: null,
              request_id: null,
              created_at: "2026-07-26T00:00:00Z",
            },
          ]
        : [],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) =>
        id === "conversation-2"
          ? conversation(id, "second conversation history")
          : conversation(id),
    );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers, signal) => {
        handlers.onToken("partial answer from first conversation");
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        });
      },
    );

    renderWithProviders("conversation-1");
    const input = screen.getByTestId("input-field");
    fireEvent.change(input, { target: { value: "Question for first" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(
      await screen.findByText("partial answer from first conversation"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Switch conversation" }));

    expect(
      await screen.findByText("second conversation history"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("partial answer from first conversation"),
    ).not.toBeInTheDocument();
    expect(input).not.toBeDisabled();
  });

  it("提交点踩反馈时包含消息 ID 与可选评论", async () => {
    const assistantMessage = {
      id: "message-1",
      role: "assistant" as const,
      content: "历史回答",
      citations: null,
      request_id: "request-1",
      created_at: "2026-07-26T00:00:00Z",
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue({
      id: "conversation-1",
      title: "历史会话",
      document_ids: [],
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      messages: [assistantMessage],
    });
    vi.spyOn(ApiClient.prototype, "getFeedback").mockResolvedValue(null);
    const submitFeedback = vi
      .spyOn(ApiClient.prototype, "submitFeedback")
      .mockResolvedValue({
        id: "feedback-1",
        request_id: "request-1",
        message_id: "message-1",
        rating: "dislike",
        comment: "引用不够准确",
        created_at: "2026-07-26T00:00:00Z",
        updated_at: "2026-07-26T00:00:00Z",
      });

    renderWithProviders("conversation-1");
    await screen.findByText("历史回答");
    fireEvent.click(screen.getByLabelText("点踩"));
    fireEvent.change(screen.getByLabelText("点踩原因"), {
      target: { value: "引用不够准确" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      expect(submitFeedback).toHaveBeenCalledWith({
        request_id: "request-1",
        rating: "dislike",
        message_id: "message-1",
        comment: "引用不够准确",
      });
    });
  });

  it("删除当前会话时不显示二次确认", async () => {
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue({
      id: "conversation-1",
      title: "待删除会话",
      document_ids: [],
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      messages: [],
    });
    const deleteConversation = vi
      .spyOn(ApiClient.prototype, "deleteConversation")
      .mockResolvedValue();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    renderWithProviders("conversation-1");
    fireEvent.click(await screen.findByTitle("点击删除当前会话"));

    await waitFor(() => {
      expect(deleteConversation).toHaveBeenCalledWith("conversation-1");
    });
    expect(confirm).not.toHaveBeenCalled();
  });

  it("问答请求参数错误时显示友好提示", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    const conversation = {
      id: "conversation-1",
      title: null,
      document_ids: [],
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      messages: [],
    };
    vi.spyOn(ApiClient.prototype, "createConversation").mockResolvedValue(
      conversation,
    );
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue(
      conversation,
    );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockRejectedValue(
      new ApiClientError(400, "question is required"),
    );

    renderWithProviders();
    const input = screen.getByTestId("input-field");
    fireEvent.change(input, { target: { value: "无效问题" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    const message = await screen.findByText(
      "请求失败：请求内容有误。question is required",
    );
    expect(message).toBeInTheDocument();
    expect(screen.queryByText(/API error/)).not.toBeInTheDocument();
  });
});
