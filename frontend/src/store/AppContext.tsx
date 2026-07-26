import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import { ApiClient } from "../api/client";

// AppContext：跨组件共享 ApiClient 实例与全局 UI 状态。
// 设计取舍（ADR 0005）：
// - 用 React Context + useState，不引入 Redux/Zustand，简化状态管理
// - 全局状态仅含当前会话 ID、当前视图（chat/settings/help），
//   其他状态由 TanStack Query 管理（缓存与失效）
export type ActiveView = "chat" | "settings" | "help";

interface AppContextValue {
  client: ApiClient;
  // 当前激活视图
  activeView: ActiveView;
  setActiveView: (view: ActiveView) => void;
  // 当前会话
  currentConversationId: string | null;
  setCurrentConversationId: (id: string | null) => void;
  // 当前会话锁定的文档 ID 列表
  currentDocumentIds: string[];
  setCurrentDocumentIds: (ids: string[]) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const client = new ApiClient();
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [currentConversationId, setCurrentConversationId] = useState<
    string | null
  >(null);
  const [currentDocumentIds, setCurrentDocumentIds] = useState<string[]>([]);

  const value: AppContextValue = {
    client,
    activeView,
    setActiveView,
    currentConversationId,
    setCurrentConversationId,
    currentDocumentIds,
    setCurrentDocumentIds,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useApp must be used within AppProvider");
  }
  return ctx;
}
