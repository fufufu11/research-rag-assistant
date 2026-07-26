import { afterEach, describe, it, expect, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatArea } from "./ChatArea";
import { AppProvider, useApp } from "../../store/AppContext";
import { ApiClient } from "../../api/client";

function DocumentSelectionProbe() {
  const { currentDocumentIds } = useApp();
  return <output data-testid="selected-document-ids">{currentDocumentIds.join(",")}</output>;
}

function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <ChatArea />
        <DocumentSelectionProbe />
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
    expect(screen.getByText("从左侧选择或新建对话开始")).toBeInTheDocument();
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
    vi.spyOn(ApiClient.prototype, "createConversation").mockResolvedValue(
      conversation,
    );
    const getConversation = vi
      .spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValue(conversation);
    vi.spyOn(ApiClient.prototype, "getFeedback").mockResolvedValue(null);

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
        });
      },
    );

    renderWithProviders();
    const input = screen.getByTestId("input-field");
    fireEvent.change(input, { target: { value: "What is RAG?" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    await waitFor(() => expect(getConversation).toHaveBeenCalled());
    expect(screen.getByText("What is RAG?")).toBeInTheDocument();

    await act(async () => {
      finishStream?.();
      await streamGate;
    });

    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    expect(screen.getByText("What is RAG?")).toBeInTheDocument();
  });
});
