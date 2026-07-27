import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiClient, ApiClientError } from "./api/client";
import type { ConversationRead } from "./api/types";
import { App } from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(ApiClient.prototype, "listDocuments").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({
        id,
        title: null,
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: [],
      }),
    );
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
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "问题输入" })).toBeEnabled(),
    );
  });

  it("选择既有会话后显示服务端历史并开放续聊", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conversation-existing",
          title: "已有讨论",
          document_ids: null,
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
          messages: null,
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue({
      id: "conversation-existing",
      title: "已有讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:01:00Z",
      messages: [
        {
          id: "message-user",
          role: "user",
          content: "这篇论文研究了什么？",
          citations: null,
          request_id: null,
          created_at: "2026-07-27T00:00:30Z",
        },
        {
          id: "message-assistant",
          role: "assistant",
          content: "论文研究了可核查的检索增强生成。",
          citations: null,
          request_id: "request-existing",
          created_at: "2026-07-27T00:01:00Z",
        },
      ],
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "已有讨论" }),
    );

    expect(
      await screen.findByText("论文研究了可核查的检索增强生成。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "问题输入" })).toBeEnabled();
  });

  it("会话历史加载期间显示状态并禁止发送", async () => {
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [
        {
          id: "conversation-loading",
          title: "加载中的讨论",
          document_ids: null,
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
          messages: null,
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockReturnValue(
      new Promise(() => undefined),
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "加载中的讨论" }),
    );

    expect(screen.getByText("正在加载会话…")).toHaveAttribute("role", "status");
    expect(screen.getByRole("textbox", { name: "问题输入" })).toBeDisabled();
  });

  it("会话历史加载失败后可重试并恢复续聊", async () => {
    const conversation = {
      id: "conversation-retry",
      title: "需要重试的讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:01:00Z",
      messages: [
        {
          id: "message-retry",
          role: "assistant" as const,
          content: "重试后恢复的历史回答",
          citations: null,
          request_id: "request-retry",
          created_at: "2026-07-27T00:01:00Z",
        },
      ],
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [{ ...conversation, messages: null }],
    });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockRejectedValueOnce(new ApiClientError(500, "database unavailable"))
      .mockResolvedValue(conversation);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "需要重试的讨论" }),
    );

    expect(
      await screen.findByText("加载会话失败：服务暂时不可用，请稍后重试。"),
    ).toHaveAttribute("role", "alert");
    expect(screen.getByRole("textbox", { name: "问题输入" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "重试加载" }));

    expect(await screen.findByText("重试后恢复的历史回答")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "问题输入" })).toBeEnabled();
  });

  it("切换后原会话继续生成且不同会话可并行问答", async () => {
    const conversation = (id: string, title: string) => ({
      id,
      title,
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    });
    const conversations = [
      conversation("conversation-a", "讨论 A"),
      conversation("conversation-b", "讨论 B"),
    ];
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: conversations,
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({
        ...conversations.find((item) => item.id === id)!,
        messages: [],
      }),
    );
    type StreamHandlers = Parameters<ApiClient["askQuestionStream"]>[1];
    const streams = new Map<
      string,
      { handlers: StreamHandlers; resolve: () => void }
    >();
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      (payload, handlers) =>
        new Promise<void>((resolve) => {
          streams.set(String(payload.conversation_id), { handlers, resolve });
        }),
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "讨论 A" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "问题 A" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(streams.has("conversation-a")).toBe(true));
    act(() => streams.get("conversation-a")!.handlers.onToken("A 的第一段"));

    fireEvent.click(screen.getByRole("button", { name: "讨论 B" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "问题 B" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(streams.has("conversation-b")).toBe(true));
    act(() => {
      streams.get("conversation-a")!.handlers.onToken("，后台继续");
      streams.get("conversation-b")!.handlers.onToken("B 的回答");
    });

    expect(screen.getByText("B 的回答")).toBeInTheDocument();
    expect(screen.queryByText(/A 的第一段/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "讨论 A" }));

    expect(await screen.findByText("A 的第一段，后台继续")).toBeInTheDocument();
  });

  it("侧栏持续标记正在后台生成的会话", async () => {
    const items = [
      {
        id: "conversation-a",
        title: "后台讨论",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
      {
        id: "conversation-b",
        title: "当前讨论",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    ];
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({ items });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({ ...items.find((item) => item.id === id)!, messages: [] }),
    );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockReturnValue(
      new Promise(() => undefined),
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "后台讨论" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "后台问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByLabelText("后台讨论正在生成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "当前讨论" }));
    expect(screen.getByLabelText("后台讨论正在生成")).toBeInTheDocument();
  });

  it("服务端确认持久化前侧栏持续标记会话正在处理", async () => {
    const conversation = {
      id: "conversation-confirming",
      title: "确认中讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    let confirmPersistence:
      | ((value: ConversationRead) => void)
      | undefined;
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...conversation, messages: [] })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            confirmPersistence = resolve;
          }),
      );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("等待服务端确认的答案");
        handlers.onDone({
          citations: [],
          request_id: "request-confirming",
          elapsed_ms: 10,
          conversation_id: "conversation-confirming",
          message_id: "assistant-confirming",
        });
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "确认中讨论" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "确认问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(
      await screen.findByLabelText("确认中讨论正在生成"),
    ).toBeInTheDocument();
    expect(input).toBeDisabled();

    act(() =>
      confirmPersistence?.({
        ...conversation,
        messages: [
          {
            id: "user-confirming",
            role: "user",
            content: "确认问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:30Z",
          },
          {
            id: "assistant-confirming",
            role: "assistant",
            content: "服务端确认的答案",
            citations: null,
            request_id: "request-confirming",
            created_at: "2026-07-27T00:01:00Z",
          },
        ],
      }),
    );

    await waitFor(() =>
      expect(
        screen.queryByLabelText("确认中讨论正在生成"),
      ).not.toBeInTheDocument(),
    );
  });

  it("按会话保留未发送的临时草稿", async () => {
    const items = [
      {
        id: "conversation-a",
        title: "讨论 A",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
      {
        id: "conversation-b",
        title: "讨论 B",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    ];
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({ items });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({ ...items.find((item) => item.id === id)!, messages: [] }),
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "讨论 A" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "A 的草稿" } });

    fireEvent.click(screen.getByRole("button", { name: "讨论 B" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "B 的草稿" } });

    fireEvent.click(screen.getByRole("button", { name: "讨论 A" }));
    expect(input).toHaveValue("A 的草稿");
    fireEvent.click(screen.getByRole("button", { name: "讨论 B" }));
    expect(input).toHaveValue("B 的草稿");
  });

  it("只停止当前会话并丢弃临时轮次后恢复问题", async () => {
    const items = [
      {
        id: "conversation-a",
        title: "后台会话",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
      {
        id: "conversation-b",
        title: "当前会话",
        document_ids: null,
        created_at: "2026-07-27T00:00:00Z",
        updated_at: "2026-07-27T00:00:00Z",
        messages: null,
      },
    ];
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({ items });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({ ...items.find((item) => item.id === id)!, messages: [] }),
    );
    const signals = new Map<string, AbortSignal | undefined>();
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      (payload, _handlers, signal) => {
        signals.set(String(payload.conversation_id), signal);
        return new Promise(() => undefined);
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "后台会话" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "后台问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    fireEvent.click(screen.getByRole("button", { name: "当前会话" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "当前问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    expect(input).toHaveValue("当前问题");
    expect(signals.get("conversation-b")?.aborted).toBe(true);
    expect(signals.get("conversation-a")?.aborted).toBe(false);
    expect(screen.getByLabelText("后台会话正在生成")).toBeInTheDocument();
    expect(screen.queryByTestId("message-user")).not.toBeInTheDocument();
  });

  it("删除生成中的会话前先终止请求并丢弃临时轮次", async () => {
    const conversation = {
      id: "conversation-generating",
      title: "待删除会话",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue({
      ...conversation,
      messages: [],
    });
    let streamSignal: AbortSignal | undefined;
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      (_payload, _handlers, signal) => {
        streamSignal = signal;
        return new Promise(() => undefined);
      },
    );
    const deleteConversation = vi
      .spyOn(ApiClient.prototype, "deleteConversation")
      .mockImplementation(async () => {
        expect(streamSignal?.aborted).toBe(true);
      });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "待删除会话" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "不会保留的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(screen.getByRole("button", { name: "删除会话 待删除会话" }));

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("conversation-generating"));
    expect(await screen.findByText("未选择会话")).toBeInTheDocument();
    expect(screen.queryByTestId("message-user")).not.toBeInTheDocument();
  });

  it("删除持久化确认中的会话前先中止回读请求", async () => {
    const conversation = {
      id: "conversation-confirm-delete",
      title: "确认中待删除会话",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    let detailCallCount = 0;
    let confirmationSignal: AbortSignal | undefined;
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      (...args: unknown[]) => {
        detailCallCount += 1;
        if (detailCallCount === 1) {
          return Promise.resolve({ ...conversation, messages: [] });
        }
        confirmationSignal = args[1] as AbortSignal | undefined;
        return new Promise<ConversationRead>(() => undefined);
      },
    );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("等待确认的临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-confirm-delete",
          elapsed_ms: 10,
          conversation_id: conversation.id,
          message_id: "assistant-confirm-delete",
        });
      },
    );
    const deleteConversation = vi
      .spyOn(ApiClient.prototype, "deleteConversation")
      .mockResolvedValue();
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "确认中待删除会话" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "确认期间删除" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByLabelText("确认中待删除会话正在生成");

    fireEvent.click(
      screen.getByRole("button", { name: "删除会话 确认中待删除会话" }),
    );

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledOnce());
    expect(confirmationSignal?.aborted).toBe(true);
    expect(screen.queryByText("确认期间删除")).not.toBeInTheDocument();
  });

  it("流完成后按 request_id 刷新并采用服务端持久化历史", async () => {
    const conversation = {
      id: "conversation-sync",
      title: "同步讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    const getConversation = vi
      .spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...conversation, messages: [] })
      .mockResolvedValueOnce({
        ...conversation,
        updated_at: "2026-07-27T00:01:00Z",
        messages: [
          {
            id: "server-user",
            role: "user",
            content: "需要持久化的问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:30Z",
          },
          {
            id: "server-assistant",
            role: "assistant",
            content: "服务端持久化答案",
            citations: null,
            request_id: "request-sync",
            created_at: "2026-07-27T00:01:00Z",
          },
        ],
      });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("临时流式答案");
        handlers.onDone({
          citations: [],
          request_id: "request-sync",
          elapsed_ms: 10,
          conversation_id: "conversation-sync",
          message_id: "server-assistant",
        });
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "同步讨论" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "需要持久化的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("服务端持久化答案")).toBeInTheDocument();
    expect(screen.queryByText("临时流式答案")).not.toBeInTheDocument();
    expect(getConversation).toHaveBeenCalledTimes(2);
    expect(input).toBeEnabled();
  });

  it("每轮成功持久化后立即刷新会话标题和排序", async () => {
    const target = {
      id: "conversation-target",
      title: null,
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const other = {
      id: "conversation-other",
      title: "其他讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:30Z",
      messages: null,
    };
    const listConversations = vi
      .spyOn(ApiClient.prototype, "listConversations")
      .mockResolvedValueOnce({ items: [other, target] })
      .mockResolvedValueOnce({
        items: [
          {
            ...target,
            title: "自动生成的标题",
            updated_at: "2026-07-27T00:01:00Z",
          },
          other,
        ],
      });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...target, messages: [] })
      .mockResolvedValueOnce({
        ...target,
        title: "自动生成的标题",
        messages: [
          {
            id: "server-user-title",
            role: "user",
            content: "标题问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:30Z",
          },
          {
            id: "server-assistant-title",
            role: "assistant",
            content: "标题答案",
            citations: null,
            request_id: "request-title",
            created_at: "2026-07-27T00:01:00Z",
          },
        ],
      });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("标题答案");
        handlers.onDone({
          citations: [],
          request_id: "request-title",
          elapsed_ms: 10,
          conversation_id: "conversation-target",
          message_id: "server-assistant-title",
        });
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "新会话" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "标题问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    const refreshed = await screen.findByRole("button", {
      name: "自动生成的标题",
    });
    const otherButton = screen.getByRole("button", { name: "其他讨论" });
    expect(
      refreshed.compareDocumentPosition(otherButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
    expect(screen.getByTestId("top-bar")).toHaveTextContent("自动生成的标题");
    expect(listConversations).toHaveBeenCalledTimes(2);
  });

  it("持久化确认后列表刷新期间仍可开始下一轮", async () => {
    const conversation = {
      id: "conversation-next-turn",
      title: "连续问答",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations")
      .mockResolvedValueOnce({ items: [conversation] })
      .mockImplementationOnce(() => new Promise(() => undefined));
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...conversation, messages: [] })
      .mockResolvedValueOnce({
        ...conversation,
        messages: [
          {
            id: "first-user",
            role: "user",
            content: "第一轮问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:10Z",
          },
          {
            id: "first-assistant",
            role: "assistant",
            content: "第一轮服务端答案",
            citations: null,
            request_id: "request-first",
            created_at: "2026-07-27T00:00:20Z",
          },
        ],
      });
    const askQuestion = vi
      .spyOn(ApiClient.prototype, "askQuestionStream")
      .mockImplementationOnce(async (_payload, handlers) => {
        handlers.onToken("第一轮临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-first",
          elapsed_ms: 10,
          conversation_id: conversation.id,
          message_id: "first-assistant",
        });
      })
      .mockImplementationOnce(() => new Promise(() => undefined));
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "连续问答" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "第一轮问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("第一轮服务端答案")).toBeInTheDocument();
    await waitFor(() => expect(input).toBeEnabled());

    fireEvent.change(input, { target: { value: "第二轮问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("第二轮问题")).toBeInTheDocument();
    expect(askQuestion).toHaveBeenCalledTimes(2);
  });

  it("同步失败时保留临时答案并禁止发送且提供重试", async () => {
    const conversation = {
      id: "conversation-sync-failed",
      title: "同步失败讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...conversation, messages: [] })
      .mockRejectedValueOnce(new ApiClientError(503, "database unavailable"));
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("仍需保留的临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-sync-failed",
          elapsed_ms: 10,
          conversation_id: "conversation-sync-failed",
          message_id: "server-assistant-failed",
        });
      },
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "同步失败讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "同步失败问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("仍需保留的临时答案")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "同步会话失败：服务暂时不可用，请稍后重试。",
      ),
    ).toHaveAttribute("role", "alert");
    expect(screen.getByRole("button", { name: "重试同步" })).toBeInTheDocument();
    expect(input).toBeDisabled();
  });

  it("重试同步成功后采用服务端历史并解除失败状态", async () => {
    const conversation = {
      id: "conversation-retry-sync",
      title: "重试同步讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...conversation, messages: [] })
      .mockRejectedValueOnce(new ApiClientError(503, "database unavailable"))
      .mockResolvedValueOnce({
        ...conversation,
        messages: [
          {
            id: "retry-user",
            role: "user",
            content: "重试同步问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:30Z",
          },
          {
            id: "retry-assistant",
            role: "assistant",
            content: "服务端确认答案",
            citations: null,
            request_id: "request-retry-sync",
            created_at: "2026-07-27T00:01:00Z",
          },
        ],
      });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("等待同步的临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-retry-sync",
          elapsed_ms: 10,
          conversation_id: "conversation-retry-sync",
          message_id: "retry-assistant",
        });
      },
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "重试同步讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "重试同步问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(await screen.findByRole("button", { name: "重试同步" }));

    expect(await screen.findByText("服务端确认答案")).toBeInTheDocument();
    expect(screen.queryByText("等待同步的临时答案")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("重试同步讨论同步失败")).not.toBeInTheDocument();
    expect(input).toBeEnabled();
  });

  it("重新验证发现同一 request_id 后自动采用服务端历史并去重", async () => {
    const failedConversation = {
      id: "conversation-auto-reconcile",
      title: "自动确认讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const otherConversation = {
      id: "conversation-auto-reconcile-other",
      title: "其他讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [failedConversation, otherConversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...failedConversation, messages: [] })
      .mockRejectedValueOnce(new ApiClientError(503, "unavailable"))
      .mockResolvedValueOnce({ ...otherConversation, messages: [] })
      .mockResolvedValueOnce({
        ...failedConversation,
        messages: [
          {
            id: "server-auto-user",
            role: "user",
            content: "自动确认问题",
            citations: null,
            request_id: null,
            created_at: "2026-07-27T00:00:10Z",
          },
          {
            id: "server-auto-assistant",
            role: "assistant",
            content: "服务端权威答案",
            citations: null,
            request_id: "request-auto-reconcile",
            created_at: "2026-07-27T00:00:20Z",
          },
        ],
      });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("等待确认的临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-auto-reconcile",
          elapsed_ms: 10,
          conversation_id: failedConversation.id,
          message_id: "server-auto-assistant",
        });
      },
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "自动确认讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "自动确认问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("button", { name: "重试同步" });

    fireEvent.click(screen.getByRole("button", { name: "其他讨论" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "自动确认讨论" }));

    expect(await screen.findByText("服务端权威答案")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("等待确认的临时答案")).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "重试同步" })).not.toBeInTheDocument();
    expect(input).toBeEnabled();
  });

  it("重新验证先于确认回读命中 request_id 时终止旧回读并解除门禁", async () => {
    const target = {
      id: "conversation-confirm-race",
      title: "确认竞态讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const other = {
      id: "conversation-confirm-race-other",
      title: "竞态切换目标",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [target, other],
    });
    let detailCallCount = 0;
    let confirmationSignal: AbortSignal | undefined;
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      (...args: unknown[]) => {
        detailCallCount += 1;
        if (detailCallCount === 1) {
          return Promise.resolve({ ...target, messages: [] });
        }
        if (detailCallCount === 2) {
          confirmationSignal = args[1] as AbortSignal | undefined;
          return new Promise<ConversationRead>(() => undefined);
        }
        if (detailCallCount === 3) {
          return Promise.resolve({ ...other, messages: [] });
        }
        return Promise.resolve({
          ...target,
          messages: [
            {
              id: "race-user",
              role: "user",
              content: "竞态问题",
              citations: null,
              request_id: null,
              created_at: "2026-07-27T00:00:10Z",
            },
            {
              id: "race-assistant",
              role: "assistant",
              content: "服务端竞态答案",
              citations: null,
              request_id: "request-confirm-race",
              created_at: "2026-07-27T00:00:20Z",
            },
          ],
        });
      },
    );
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("竞态临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-confirm-race",
          elapsed_ms: 10,
          conversation_id: target.id,
          message_id: "race-assistant",
        });
      },
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "确认竞态讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "竞态问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByLabelText("确认竞态讨论正在生成");

    fireEvent.click(screen.getByRole("button", { name: "竞态切换目标" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认竞态讨论" }));

    expect(await screen.findByText("服务端竞态答案")).toBeInTheDocument();
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.queryByText("竞态临时答案")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("确认竞态讨论正在生成"),
    ).not.toBeInTheDocument();
    expect(confirmationSignal?.aborted).toBe(true);
  });

  it("侧栏持续标记同步失败的会话且不阻塞其他会话", async () => {
    const failedConversation = {
      id: "conversation-failed",
      title: "失败讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const healthyConversation = {
      ...failedConversation,
      id: "conversation-healthy",
      title: "正常讨论",
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [failedConversation, healthyConversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce({ ...failedConversation, messages: [] })
      .mockRejectedValueOnce(new ApiClientError(503, "database unavailable"))
      .mockResolvedValueOnce({ ...healthyConversation, messages: [] });
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      async (_payload, handlers) => {
        handlers.onToken("失败会话的临时答案");
        handlers.onDone({
          citations: [],
          request_id: "request-failed",
          elapsed_ms: 10,
          conversation_id: "conversation-failed",
          message_id: "message-failed",
        });
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "失败讨论" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "失败问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("button", { name: "重试同步" });

    fireEvent.click(screen.getByRole("button", { name: "正常讨论" }));

    expect(await screen.findByLabelText("失败讨论同步失败")).toBeInTheDocument();
    await waitFor(() => expect(input).toBeEnabled());
  });

  it("立即显示缓存历史但重新验证成功前禁止发送", async () => {
    const conversationA = {
      id: "conversation-cached",
      title: "缓存讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const conversationB = {
      ...conversationA,
      id: "conversation-away",
      title: "其他会话",
    };
    const detail = (content: string) => ({
      ...conversationA,
      messages: [
        {
          id: "cached-assistant",
          role: "assistant" as const,
          content,
          citations: null,
          request_id: "request-cached",
          created_at: "2026-07-27T00:00:30Z",
        },
      ],
    });
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversationA, conversationB],
    });
    let finishRevalidation: ((value: ReturnType<typeof detail>) => void) | undefined;
    vi.spyOn(ApiClient.prototype, "getConversation")
      .mockResolvedValueOnce(detail("缓存中的历史答案"))
      .mockResolvedValueOnce({ ...conversationB, messages: [] })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishRevalidation = resolve;
          }),
      );
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "缓存讨论" }));
    const input = screen.getByRole("textbox", { name: "问题输入" });
    expect(await screen.findByText("缓存中的历史答案")).toBeInTheDocument();
    await waitFor(() => expect(input).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "其他会话" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "缓存讨论" }));

    expect(screen.getByText("缓存中的历史答案")).toBeInTheDocument();
    expect(input).toBeDisabled();

    act(() => finishRevalidation?.(detail("重新验证后的服务端答案")));
    expect(await screen.findByText("重新验证后的服务端答案")).toBeInTheDocument();
    await waitFor(() => expect(input).toBeEnabled());
  });

  it("后台生成失败只恢复目标会话草稿并在侧栏标记失败", async () => {
    const conversationA = {
      id: "conversation-stream-failed",
      title: "生成失败讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    const conversationB = {
      ...conversationA,
      id: "conversation-stream-healthy",
      title: "当前健康讨论",
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversationA, conversationB],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockImplementation(
      async (id) => ({
        ...(id === conversationA.id ? conversationA : conversationB),
        messages: [],
      }),
    );
    let failStream: ((reason: unknown) => void) | undefined;
    vi.spyOn(ApiClient.prototype, "askQuestionStream").mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          failStream = reject;
        }),
    );
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "生成失败讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "只属于失败会话的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    fireEvent.click(screen.getByRole("button", { name: "当前健康讨论" }));
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "健康会话草稿" } });
    await act(async () => {
      failStream?.(new ApiClientError(503, "model unavailable"));
    });

    expect(input).toHaveValue("健康会话草稿");
    expect(
      screen.getByLabelText("生成失败讨论生成失败"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "生成失败讨论" }));
    expect(input).toHaveValue("只属于失败会话的问题");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "生成回答失败：服务暂时不可用，请稍后重试。",
    );
  });

  it("硬刷新中止本页请求且不恢复选择和临时任务", async () => {
    const conversation = {
      id: "conversation-refresh",
      title: "刷新前讨论",
      document_ids: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      messages: null,
    };
    vi.spyOn(ApiClient.prototype, "listConversations").mockResolvedValue({
      items: [conversation],
    });
    vi.spyOn(ApiClient.prototype, "getConversation").mockResolvedValue({
      ...conversation,
      messages: [],
    });
    let streamSignal: AbortSignal | undefined;
    const askQuestion = vi
      .spyOn(ApiClient.prototype, "askQuestionStream")
      .mockImplementation((_payload, _handlers, signal) => {
        streamSignal = signal;
        return new Promise(() => undefined);
      });
    const firstPage = render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "刷新前讨论" }),
    );
    const input = screen.getByRole("textbox", { name: "问题输入" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "刷新即丢弃的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("刷新即丢弃的问题");

    firstPage.unmount();
    expect(streamSignal?.aborted).toBe(true);

    render(<App />);
    expect(await screen.findByText("未选择会话")).toBeInTheDocument();
    expect(screen.queryByText("刷新即丢弃的问题")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("刷新前讨论正在生成"),
    ).not.toBeInTheDocument();
    expect(askQuestion).toHaveBeenCalledTimes(1);
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
