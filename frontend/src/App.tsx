import { Sidebar } from "./components/Sidebar/Sidebar";
import { ChatArea } from "./components/ChatArea/ChatArea";
import { Settings } from "./components/Settings/Settings";
import { Help } from "./components/Help/Help";
import { useApp } from "./store/AppContext";

// App：React SPA 根组件
// 整合 T2-T8：260px 左侧栏 + 右侧主区（chat / settings / help 三视图切换）
// 设计稿：.trae/handoffs/ui_claude_v1.html
function AppRoutes() {
  const { activeView } = useApp();
  return (
    <div className="app" data-testid="app-root">
      <Sidebar />
      {activeView === "chat" && <ChatArea />}
      {activeView === "settings" && <Settings />}
      {activeView === "help" && <Help />}
    </div>
  );
}

export function App() {
  return <AppRoutes />;
}
