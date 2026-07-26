import { ModelDropdown } from "../ModelDropdown/ModelDropdown";

// ChatArea：右侧主聊天区（暖米色背景）
// T2 范围：仅落地布局骨架——顶部栏 + 模型下拉占位 + 居中 720px 内容占位。
// 消息流（user/assistant 气泡）+ 输入栏 + 引用卡片 由 T5/T6/T7 接入。
// 设计稿：.trae/handoffs/ui_claude_v1.html
export function ChatArea() {
  return (
    <main className="chat-area" data-testid="chat-area">
      <div className="top-bar" data-testid="top-bar">
        <ModelDropdown />
        <div className="conversation-meta">
          <span>未选择会话</span>
        </div>
      </div>

      <div className="messages-wrap">
        <div className="content-placeholder" data-testid="content-placeholder">
          <h1>科研文献智能问答</h1>
          <p>从左侧选择或新建对话开始</p>
        </div>
      </div>
    </main>
  );
}
