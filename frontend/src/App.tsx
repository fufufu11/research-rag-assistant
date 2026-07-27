import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiClient } from "./api/client";
import type { ConversationRead } from "./api/types";
import { Sidebar } from "./components/Sidebar/Sidebar";
import { ChatAreaView } from "./components/ChatArea/ChatArea";
import { useChat } from "./hooks/useChat";

// App：React SPA 根组件
// T2 阶段：渲染 Claude 风格基础布局骨架——260px 左侧栏 + 右侧主聊天区。
// 业务功能（文档管理 / 会话管理 / SSE 流式问答 / 反馈）由 T3-T7 接入。
// 设计稿：.trae/handoffs/ui_claude_v1.html
interface AppProps {
  client?: ApiClient;
}

export function App({ client: providedClient }: AppProps) {
  const [client] = useState(() => providedClient ?? new ApiClient());
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AppContent client={client} />
    </QueryClientProvider>
  );
}

function AppContent({ client }: { client: ApiClient }) {
  const [currentConversation, setCurrentConversation] =
    useState<ConversationRead | null>(null);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const chat = useChat({
    client,
    conversationId: currentConversation?.id ?? null,
  });

  return (
    <div className="app" data-testid="app-root">
      <Sidebar
        client={client}
        currentConversationId={currentConversation?.id ?? null}
        streamingConversationIds={chat.streamingConversationIds}
        failedConversationIds={chat.failedConversationIds}
        generationFailedConversationIds={chat.generationFailedConversationIds}
        selectedDocumentIds={selectedDocumentIds}
        onSelectedDocumentIdsChange={setSelectedDocumentIds}
        onSelectConversation={setCurrentConversation}
        onConversationDeleteStart={chat.discardConversation}
        onConversationDeleted={(id) => {
          setCurrentConversation((conversation) =>
            conversation?.id === id ? null : conversation,
          );
        }}
      />
      <ChatAreaView
        client={client}
        currentConversation={currentConversation}
        canChat={currentConversation !== null}
        chat={chat}
      />
    </div>
  );
}
