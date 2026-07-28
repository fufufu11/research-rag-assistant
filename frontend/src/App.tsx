import { Sidebar } from "./components/Sidebar/Sidebar";
import { ChatArea } from "./components/ChatArea/ChatArea";
import { Settings } from "./components/Settings/Settings";
import { Help } from "./components/Help/Help";
import { useEffect } from "react";
import { useApp } from "./store/AppContext";

// App：React SPA 根组件
// 整合 T2-T8：260px 左侧栏 + 右侧主区（chat / settings / help 三视图切换）
// 设计稿：.trae/handoffs/ui_claude_v1.html
function AppRoutes() {
  const { activeView, isMobileNavOpen, setIsMobileNavOpen } = useApp();

  useEffect(() => {
    if (!isMobileNavOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsMobileNavOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isMobileNavOpen, setIsMobileNavOpen]);

  return (
    <div className="app" data-testid="app-root">
      <button
        type="button"
        className="mobile-menu-button"
        aria-label={isMobileNavOpen ? "关闭导航" : "打开导航"}
        aria-controls="app-sidebar"
        aria-expanded={isMobileNavOpen}
        title={isMobileNavOpen ? "关闭导航" : "打开导航"}
        onClick={() => setIsMobileNavOpen(!isMobileNavOpen)}
      >
        <span aria-hidden="true">☰</span>
      </button>
      <button
        type="button"
        className={`sidebar-backdrop ${isMobileNavOpen ? "visible" : ""}`}
        aria-label="关闭导航"
        tabIndex={isMobileNavOpen ? 0 : -1}
        onClick={() => setIsMobileNavOpen(false)}
      />
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
