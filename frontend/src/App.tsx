import { Sidebar } from "./components/Sidebar/Sidebar";
import { ChatArea } from "./components/ChatArea/ChatArea";

// App：React SPA 根组件
// T2 阶段：渲染 Claude 风格基础布局骨架——260px 左侧栏 + 右侧主聊天区。
// 业务功能（文档管理 / 会话管理 / SSE 流式问答 / 反馈）由 T3-T7 接入。
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function App() {
  return (
    <div className="app" data-testid="app-root">
      <Sidebar />
      <ChatArea />
    </div>
  );
}
