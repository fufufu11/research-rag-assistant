# ADR 0005: 切换 UI 层到 React SPA，废弃 Streamlit

## 状态

Accepted（2026-07-26）

## 背景

阶段 0-11.6 后端能力已完整（FastAPI + 文档管理 + SSE 流式问答 + 多轮会话 + 反馈闭环 + 安全加固），UI 层是 Streamlit（`src/research_rag/ui/app.py`）。阶段三过渡切片（PR #122 / v3.2）尝试把 Claude 静谧极简风格落地到 Streamlit，验证了 Streamlit 的硬限制：

1. **`st.chat_message` 强制 avatar + 气泡容器** → 无法做 Claude 标志性的「assistant 纯段落无气泡」
2. **`st.columns` 左侧栏无法做深色背景** → DOM 层级太深，CSS 注入只能改内层，无法把侧栏改成 `#1c1815` 深棕色
3. **微交互只能靠 JS 注入每个组件** → 复制按钮、引用卡片 hover、消息入场 stagger 等都要 `streamlit.components.v1.html` 注入，维护噩梦
4. **CSS 注入覆盖默认样式** → 能达成 60-70% 还原度，但无法突破框架约束

已有设计稿 [.trae/handoffs/ui_claude_v1.html](file:///d:/CODE/research-rag-assistant/.trae/handoffs/ui_claude_v1.html) 是纯 HTML + CSS + JS 实现的 Claude.ai 风格界面，包含暖米色背景、深棕侧栏、赤陶土强调色、Newsreader 衬线消息流、pill 输入栏、引用卡片彩色边框等。Streamlit 无法 100% 落地此设计稿。

## 决策

**用 React 18 + TypeScript + Vite 的 SPA 替换 Streamlit UI 层**，首 ticket 即删除 `src/research_rag/ui/`。

### 技术栈选型

| 维度 | 选择 | 理由 |
|---|---|---|
| 构建工具 | Vite 5 | 快、React 官方推荐 |
| 框架 | React 18 + TypeScript | 主流前端栈，类型安全 |
| 状态管理 | TanStack Query（React Query） | 适合 API 状态缓存，与后端 REST API 配合好 |
| 样式 | 纯 CSS + CSS 变量 | 避免引入 Tailwind 增加复杂度；CSS 变量足以实现 Claude 风格主题 |
| 字体 | Google Fonts（Newsreader + IBM Plex Sans + IBM Plex Mono） | 与设计稿一致 |
| markdown 渲染 | react-markdown + remark-gfm | 主流方案 |
| 路由 | 不用 React Router | 单页应用，用 useState 切换 view（侧栏 + 主区） |
| 测试 | Vitest + React Testing Library + jsdom | 与项目 TDD 约定一致，不引入 E2E |
| 部署 | FastAPI 新增 `/web` 路由托管 `frontend/dist` | 生产单服务部署，与现有 docker-compose 集成容易 |

### 认证策略

- `API_KEY_ENABLED` 默认 false（开发不需要）
- 前端 ApiClient 从 localStorage 读 key（空则不传 Authorization header）
- 生产需要认证时，用户在设置页填入 key
- 后续可演进为 JWT 用户系统（独立 issue）

### Streamlit 废弃时机

**首 ticket 即删除** `src/research_rag/ui/`（Streamlit app.py + api_client.py + 相关测试）。理由：
- 双轨并行维护成本高
- React 骨架期间无可用 UI，但后端 API 可用 curl/Postman 验证
- 避免「先双轨再清理」的过渡期技术债

## 后果

- **正面**：
  - UI 自由度 100%，设计稿可原样落地
  - 复用现有后端 API，无后端改动（仅新增 `/web` 路由 + CORS 配置）
  - 专业前端栈，TypeScript 类型安全
  - 删除 Streamlit 后减少 Python 依赖与维护负担
- **负面 / 已知局限**：
  - 引入独立前端项目 + 构建管线，工作量中等偏高
  - CI 新增 frontend job，PR 必须前后端 CI 都绿
  - React 骨架开发期间无可用 UI（首 ticket 后到 SSE 问答跑通前的窗口期）
- **风险**：
  - 前端测试覆盖不足导致回归 → 首 ticket 即引入 Vitest + RTL，每个功能模块 TDD
  - 前后端 API 契约漂移 → 前端 `api/types.ts` 严格对应 `schemas.py`，后端 schema 改动需同步前端类型
  - CORS 配置错误导致开发环境不可用 → 首 ticket 即配置后端 CORS 允许 5173
- **未来演进**：
  - 若需 URL 可访问特定会话（书签/分享），可后续引入 React Router
  - 若需 JWT 用户系统，可独立 issue 接入
  - 若需 E2E 测试，可后续引入 Playwright

## 关联

- 设计稿：[.trae/handoffs/ui_claude_v1.html](file:///d:/CODE/research-rag-assistant/.trae/handoffs/ui_claude_v1.html)
- 现有后端 API：[src/research_rag/api/routes/](file:///d:/CODE/research-rag-assistant/src/research_rag/api/routes)
- 现有 Streamlit UI（待删除）：[src/research_rag/ui/](file:///d:/CODE/research-rag-assistant/src/research_rag/ui)
- 阶段三过渡切片 PR：#122
- 当前状态：v3.2，938 测试
