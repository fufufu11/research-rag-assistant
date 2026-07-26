// ModelDropdown：顶部模型选择下拉占位（T2）
// 设计取舍（参见 Issue #125）：
// - 用 <select disabled> 单元素，仅展示「research-rag」字样，不响应切换
// - 真实切换需后端补 /api/v1/config 端点返回可用模型列表，留待后续 issue
// - 与设计稿 .model-dropdown 容器组合呈现：dot + select + 占位 badge
export function ModelDropdown() {
  return (
    <div className="model-dropdown" data-testid="model-dropdown">
      <span className="dot" aria-hidden="true" />
      <select disabled aria-label="模型选择（占位）" defaultValue="research-rag">
        <option value="research-rag">research-rag</option>
      </select>
      <span className="badge">占位</span>
    </div>
  );
}
