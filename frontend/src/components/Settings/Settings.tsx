import { useState } from "react";
import { useApp } from "../../store/AppContext";

// Settings：设置页
// - API Key 管理（输入 + 保存 + 清除）
// - API key 持久化到 localStorage，ApiClient 自动读取并附加 Authorization header
// - 后端 /web 路由生产同源托管，开发环境走 vite proxy
// 设计稿：基于 Claude 风格 .settings-page
export function Settings() {
  const { client } = useApp();
  const [apiKey, setApiKey] = useState(() => client.apiKey ?? "");
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    const trimmed = apiKey.trim();
    if (trimmed) {
      client.setApiKey(trimmed);
    } else {
      client.setApiKey(null);
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const handleClear = () => {
    setApiKey("");
    client.setApiKey(null);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="settings-page" data-testid="settings-page">
      <div className="settings-container">
        <h1>设置</h1>
        <p className="subtitle">配置 API Key 与其他偏好</p>

        <div className="settings-section">
          <h2>API Key</h2>
          <p className="hint">
            后端需 Authorization: Bearer &lt;key&gt; 才能访问受保护端点。
            API Key 持久化到浏览器 localStorage，不会上传到服务器。
          </p>
          <input
            type="password"
            className="api-key-input"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="输入 API Key（可选）"
            aria-label="API Key 输入框"
            data-testid="api-key-input"
          />
          <div className="settings-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={handleSave}
              data-testid="save-api-key-btn"
            >
              {saved ? "✓ 已保存" : "保存"}
            </button>
            <button
              type="button"
              className="btn-danger"
              onClick={handleClear}
              data-testid="clear-api-key-btn"
            >
              清除
            </button>
          </div>
        </div>

        <div className="settings-section">
          <h2>关于</h2>
          <p className="hint">
            research·rag — 科研文献智能问答系统。基于 RAG（检索增强生成）
            架构，支持 PDF 文档上传与流式问答。
          </p>
        </div>
      </div>
    </div>
  );
}
