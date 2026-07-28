// Help：帮助页
// 项目说明 + 使用指南 + 快捷键
export function Help() {
  return (
    <div className="help-page" data-testid="help-page">
      <div className="help-container">
        <h1>帮助</h1>
        <p className="subtitle">使用指南与常见问题</p>

        <div className="help-section">
          <h2>项目简介</h2>
          <p>
            research·rag 是一个基于 RAG（检索增强生成）架构的科研文献智能问答系统。
            上传 PDF 文档后，即可针对文档内容进行问答，
            支持流式输出与引用溯源。
          </p>
        </div>

        <div className="help-section">
          <h2>快速上手</h2>
          <ul>
            <li>点击输入栏左侧 <code>+</code> 按钮上传 PDF 文档</li>
            <li>在输入框中输入问题，按 <code>Enter</code> 发送</li>
            <li>AI 回答下方会显示引用卡片，含来源文档名与页码</li>
            <li>点击 <code>复制</code> 按钮复制回答全文</li>
            <li>点击 <code>赞</code> / <code>踩</code> 提交反馈，再次点击可取消</li>
            <li>左侧栏可切换历史会话或新建对话</li>
          </ul>
        </div>

        <div className="help-section">
          <h2>快捷键</h2>
          <ul>
            <li><code>Enter</code> — 发送消息</li>
            <li><code>Shift + Enter</code> — 输入换行</li>
          </ul>
        </div>

        <div className="help-section">
          <h2>API Key</h2>
          <p>
            若后端开启了 API Key 鉴权，请到 <code>设置</code> 页输入 API Key。
            API Key 保存在浏览器 localStorage，仅在请求时附加到
            <code>Authorization: Bearer</code> header。
          </p>
        </div>

        <div className="help-section">
          <h2>免责声明</h2>
          <p>
            AI 可能出错，请核实重要信息。引用卡片指向原始文档片段，
            可对照原文确认。
          </p>
        </div>
      </div>
    </div>
  );
}
