// Sidebar：左侧栏组件（260px 深棕背景）
// T2 范围：仅落地布局骨架与占位，业务数据（会话列表 / 文档列表）由 T3/T4 接入。
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function Sidebar() {
  return (
    <aside className="sidebar" data-testid="sidebar">
      <div className="sidebar-header">
        <span className="logo-dot" aria-hidden="true" />
        <span>
          research<span className="accent">·</span>rag
        </span>
      </div>

      <button type="button" className="new-chat-btn" data-testid="new-chat-btn">
        <span className="star" aria-hidden="true">
          ✦
        </span>
        <span>新建对话</span>
      </button>

      <input
        type="text"
        className="search-input"
        placeholder="搜索会话…"
        aria-label="搜索会话"
        data-testid="search-input"
      />

      {/* 上层分组：历史会话 + 文档库（T3/T4 接入真实数据） */}
      <nav className="nav-section" data-testid="nav-history">
        <button type="button" className="nav-section-title">
          <span>历史会话</span>
          <span className="arrow" aria-hidden="true">
            ▾
          </span>
        </button>
        <div className="nav-empty">暂无会话</div>
      </nav>

      <nav className="nav-section" data-testid="nav-documents">
        <button type="button" className="nav-section-title">
          <span>文档库</span>
          <span className="arrow" aria-hidden="true">
            ▾
          </span>
        </button>
        <div className="nav-empty">暂无文档</div>
      </nav>

      {/* 下层分组：设置 + 帮助（T8 接入真实页面） */}
      <div className="sidebar-footer" data-testid="sidebar-footer">
        <button type="button" className="footer-btn" data-testid="footer-settings">
          <span className="icon" aria-hidden="true">
            ⚙
          </span>
          <span>设置</span>
        </button>
        <button type="button" className="footer-btn" data-testid="footer-help">
          <span className="icon" aria-hidden="true">
            ?
          </span>
          <span>帮助</span>
        </button>
      </div>
    </aside>
  );
}
