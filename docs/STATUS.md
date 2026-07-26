# 项目状态

## 当前状态

`v4.1` — 阶段 0-10.3 + 11.1 + 11.2 + 11.3 + 11.4 + 11.5 + 11.6 全部完成（阶段 11.6 生产安全加固 6 个切片 A-F 全部完成：#97 #98 #99 #101 #100 #102）；阶段 10.2 前端补充（PR #87）完成反馈按钮接入；历史消息反馈 prefactor（PR #93 / Issue #89）完成 `Message.request_id` 列 + ADR 0003 演进；历史消息反馈写入读出（PR #94 / Issue #90）完成 `_persist_turn` 透传 `request_id` 到 assistant `Message` + `MessageRead` schema 暴露 `request_id` + `add_message` 签名扩展；历史消息反馈前端 model+client（PR #95 / Issue #91）完成 `MessageInfo.request_id` 字段 + `_parse_message` 解析 + `ApiClient.get_feedback` 404 转 None；历史消息反馈前端 UI 渲染（PR #96 / Issue #92）完成 `_render_feedback_buttons` 扩展到历史消息循环 + `_init_feedback_state_for_history` 批量初始化反馈状态 + 旧消息隐藏按钮，历史消息反馈端到端体验闭环完成；阶段 11.6 切片 A-F（PR #103 #105 #104 #106 #107 #108）完成生产安全加固全部 6 个切片；**UI 体验优化阶段二**（基于 ChatGPT 界面截图的二轮迭代，5 个 ticket #109-#113 全部 squash 合并到 main，905 → 938 测试零回归）；**UI 体验优化阶段三过渡切片**（PR #122 / Issue #121）将 Claude 静谧极简风格落地到 Streamlit `app.py`：新增 `_get_claude_style_css()` 函数注入完整 CSS（Google Fonts Newsreader + IBM Plex Sans/Mono + CSS 变量暖米色背景 + 赤陶土强调色 + 噪声纹理 + 衬线消息流 + pill 输入栏 + 引用卡片彩色边框）+ 改造 `_render_citations_inline()` 为 HTML 双列卡片网格（4 色循环彩色左边框 + hover 抬升）+ 保留 `_get_chat_layout_css()` 向后兼容；**React SPA 阶段 T1**（Issue #124）创建 `frontend/` Vite + React 18 + TypeScript 项目骨架（`api/client.ts` ApiClient 封装 + `api/types.ts` 严格对应 `schemas.py` + `App.tsx` hello world + API 健康检查），配置 Vitest + React Testing Library + jsdom（9 个前端测试），新增 `.github/workflows/ci.yml` frontend job（与 python job 并行），后端 CORS 保留 5173，**删除** `src/research_rag/ui/` Streamlit 层 + 8 个相关测试文件 + `pyproject.toml` 移除 streamlit/requests 依赖 + mypy override 清理，`test_auth.py` 移除 `TestApiClientApiKey` 类（前端 `client.test.ts` 替代），后端测试 938 → 829 零回归 + 前端 9 测试，CI 四项全绿（Lint / Type Check / Test / Frontend）；**React SPA 阶段 T2**（Issue #125 / PR #133）落地 Claude 风格主题与基础布局骨架：新增 `frontend/src/styles/claude-theme.css` 完整 CSS 变量（背景/表面色、文字色、赤陶土强调色 `#c96442`、边框、引用卡片彩色边框 `--cite-1` ~ `--cite-4`、阴影、布局尺寸 `--sidebar-width=260px` / `--content-max-width=720px`）+ 重写 `globals.css` 为 `@import` + `.app` grid 主布局（260px 左侧栏 + 1fr 右侧主区）+ 左侧栏深棕 `#1c1815` + 右侧主区暖米色 `#faf9f7` + 模型下拉占位 + 居中收窄 720px 内容占位样式；`frontend/index.html` 引入 Google Fonts（Newsreader + IBM Plex Sans + IBM Plex Mono，含 preconnect）；新增 `components/Sidebar/Sidebar.tsx`（header logo dot + research·rag 品牌 / 新建对话按钮 / 搜索输入框 / 历史会话与文档库分组占位空状态 / 下层设置 + 帮助按钮）+ `components/ChatArea/ChatArea.tsx`（顶部栏 ModelDropdown + 未选择会话提示 + 居中 720px 内容占位「科研文献智能问答」）+ `components/ModelDropdown/ModelDropdown.tsx`（`<select disabled>` 单元素占位，仅展示 research-rag + 占位 badge，不响应切换）；重写 `App.tsx` 为 `<div class="app"><Sidebar /><ChatArea /></div>` 双栏布局；24 个新增前端测试（App 3 + Sidebar 6 + ChatArea 4 + ModelDropdown 4 + claude-theme 9，CSS 变量测试用 `fs.readFileSync` 读文件原文断言绕过 vitest `css: false` 限制），后端 829 测试零回归 + 前端 9 → 33 测试，CI 四项全绿。后续 T3-T8 逐步接入文档管理 / 会话管理 / SSE 流式问答 / 反馈 / 部署集成。

## 已完成功能

### PDF 解析与切分

- 按页提取文本并保留页码（PyMuPDF）
- 跨页切分：合并连续页文本后统一切分（阶段 8.2），`Chunk` 用 `start_page` + `end_page` 溯源
- 页内文本清洗与带重叠的 Chunk 切分（LangChain `RecursiveCharacterTextSplitter`）
- 异常处理：文件不存在、损坏 PDF、空 PDF

### Embedding 与向量检索

- 基于 LangChain 的 Embedding 适配器（默认 `BAAI/bge-small-zh-v1.5`，中文优化；英文或中英文混合场景可通过 `EMBEDDING_MODEL` 环境变量切换为 `BAAI/bge-small-en-v1.5` 或 `BAAI/bge-m3` 多语言模型）
- 三模式 provider：`local` / `dashscope` / `jina`，通过 `EMBEDDING_PROVIDER` 环境变量切换（阶段 8.4）
- Top-K 向量检索，按余弦相似度降序
- Qdrant 向量数据库（未配置时回退到 `InMemoryVectorStore`）
- BGE Reranker 重排序（Cross-Encoder 两阶段检索，默认关闭，`RERANKER_ENABLED=true` 启用）
- BM25 混合检索（`rank_bm25` + 向量 + 加权 RRF 融合，`vector_weight=2.0`，阶段 8.3）

### 大模型问答与可靠引用

- OpenAI 兼容协议客户端，超时与重试由 httpx 实现指数退避
- 服务端引用映射：模型输出 `[C1]` 编号，服务端映射真实文档名、页码与片段
- 证据不足时拒绝猜测（`[INSUFFICIENT_EVIDENCE]`）
- 流式输出（SSE）：FastAPI `StreamingResponse` + LangChain `astream` + SSE 协议（token/done/error 三类事件），Streamlit `st.write_stream` 接收（阶段 9.1）
- 多轮对话：DB 持久化会话历史 + 查询改写 + 历史截断双重保护 + 会话级文档范围锁定，流式/非流式双路径都支持（阶段 9.2）

### FastAPI 与数据库

- 文档管理 API：上传 / 列表 / 详情 / 删除
- 问答 API：`POST /api/v1/queries`（支持 `stream=true` 流式、`conversation_id` 多轮）
- 会话管理 API：`POST/GET/DELETE /api/v1/conversations`、`GET /api/v1/conversations/{id}/messages`（阶段 9.2）
- 用户反馈 API：`POST /api/v1/feedback`（Upsert，201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`、`GET /api/v1/feedback?rating=&conversation_id=&limit=`（列表筛选）、`DELETE /api/v1/feedback/{request_id}`（阶段 10.2）
- SQLAlchemy 2.0 数据模型（Document / Chunk / Conversation / Message / Feedback）+ Alembic 迁移
- sha256 去重、文件落盘、状态机（`pending → processing → ready / failed`）
- 全局异常处理器统一映射业务异常到 HTTP 状态码

### 演示界面（React SPA）

- **React SPA**（ADR 0005，Issue #124/#125/#135）：`frontend/` Vite + React 18 + TypeScript，落地 Claude 静谧极简风格（设计稿 `.trae/handoffs/ui_claude_v1.html`）
  - **T2 已完成**：260px 左侧栏（深棕 `#1c1815`）+ 右侧主聊天区（暖米色 `#faf9f7`）+ 顶部模型下拉占位 + 居中收窄 720px 内容区
  - **T3 已完成**：TanStack Query 文档列表 + 单 PDF 上传 + 无确认删除 + 四种处理状态与失败原因 + 结构化错误友好提示
  - 左侧栏：文档区默认展开且可折叠，展示名称、页数、状态和失败原因；上传或删除成功后自动刷新列表
  - 右侧主区：顶部栏 ModelDropdown（`<select disabled>` 占位）+ 未选择会话提示 + 内容占位「科研文献智能问答」+ 底部单 PDF 上传入口
  - Google Fonts：Newsreader（衬线消息正文）+ IBM Plex Sans（UI）+ IBM Plex Mono（代码/ID）
  - CSS 变量主题：`claude-theme.css` 定义完整设计 token（背景/表面色、文字色、赤陶土强调色 `#c96442`、边框、引用卡片彩色边框 `--cite-1` ~ `--cite-4`、阴影、布局尺寸）
- **历史**：Streamlit UI 层已在 T1 删除（`src/research_rag/ui/` 移除 + streamlit/requests 依赖清理），UI 体验优化阶段一/二/三过渡切片（#72 / #109-#113 / #121）已随 Streamlit 退役归档
- 会话管理、文档范围锁定、引用卡片、流式输出等业务功能将在 T4-T8 逐步接入 React SPA

### UI 体验优化阶段二（#109-#113）

基于 ChatGPT 界面截图的二轮 UI 迭代，5 个 ticket 全部 squash 合并到 main，905 → 938 测试基线零回归（+33 新增）：

- **#109 左侧导航重构**（commit `5b6cbef`，+6 测试）：图标分组 + 可折叠会话/文档列表
  - 新增 `_render_nav_section` / `_is_nav_section_expanded` / `_is_sidebar_collapsed` 纯函数
  - 上层分组：新建会话 / 搜索会话 / 历史会话列表（可折叠，默认展开）/ 文档列表（可折叠，默认展开，支持选择/删除但不上传）
  - 下层分组：设置 / 帮助
- **#112 输入栏「+」上传按钮+底部免责声明**（commit `e589cc0`，+8 测试）
  - 新增 `_render_input_toolbar` / `_is_valid_pdf_filename` 纯函数 + `_UPLOAD_DISCLAIMER` 常量
  - 输入栏左侧「+」按钮触发 PDF 上传（移除原左侧导航的文档管理上传入口，统一到输入栏）
  - 底部免责声明：「AI 可能出错，请核查重要信息」
- **#111 对话区居中+宽度收窄布局**（commit `012624a`，+5 测试）
  - 新增 `_get_chat_layout_css` 纯函数注入 CSS
  - 对话区域居中 + 最大宽度收窄（移动宽屏下阅读体验改善）
- **#113 AI 回复复制按钮**（PR #117，commit `9aba590`，+8 测试）
  - 新增 `_render_copy_button` / `_strip_markdown_to_plain_text` 纯函数
  - 用 `streamlit.components.v1.html` 注入 `navigator.clipboard.writeText` JS + Toast 提示复制成功
  - 复制内容为剥离 Markdown 标记后的纯文本
- **#110 顶部模型选择下拉（占位）**（PR #118，commit `cc27531`，+6 测试）
  - 新增 `_render_model_dropdown` / `_get_current_model_name` / `_get_model_dropdown_options` 纯函数
  - `st.selectbox` + `disabled=True` 单元素 options 实现「展示但不切换」的占位下拉
  - 真切换需后端补 `/api/v1/config` 端点，留待后续 issue
- **设计取舍**：纯函数与 Streamlit side-effect 分离（参考 `tests/unit/test_ui_*.py` 测试风格），用 `_make_session_state` 工厂函数模拟 `st.session_state`，`monkeypatch.setenv/delenv` 处理环境变量，函数内延迟导入避免模块加载副作用
- **不在范围**：模型真切换（需后端补端点）、用户登录页（需 JWT 用户系统）、移动端响应式适配

### 评测与质量优化

- 30 条英文检索评测数据集（3 篇英文 AI 经典论文，多论文合并库检索）
- 30 条中文检索评测数据集（3 篇 ChinaXiv 中文 AI 论文，阶段 8.4）
- Hit@1 / Hit@5 / MRR / 平均检索耗时指标
- 5 组参数对比实验，支持中英文 Embedding 模型切换
- 英文最优 Hit@5=76.7%、MRR=0.607（chunk-500-overlap-0 + bge-small-en + BM25 + reranker），详见 [评测报告](./evaluation_report.md)
- 中文最优 Hit@5=90.0%、MRR=0.783（chunk-500-overlap-160 + bge-small-zh + reranker），详见 [中文评测报告](./evaluation_report_zh.md)
- 答案质量评测（LLM-as-judge 自实现，阶段 9.3）：四项指标 1-5 分（忠实度 / 相关性 / 完整性 / 引用正确性），纯函数与编排分离，judge LLM 支持 `JUDGE_LLM_*` 环境变量覆盖避免同模型自评偏差
- 答案质量结果（DeepSeek-V3.2 评测）：英文忠实度=5.00 相关性=4.96 完整性=4.62 引用正确性=4.54；中文忠实度=5.00 相关性=5.00 完整性=4.97 引用正确性=5.00，详见 [答案质量报告](./answer_quality_report.md) / [中文报告](./answer_quality_report_zh.md)

### 可观测性

- Langfuse 全链路追踪（阶段 10.1）：自部署 + LangChain CallbackHandler 集成
- 环境变量开关 no-op 优先：`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST` 三项非空才启用，未配置时零开销
- `QaService.answer` / `answer_stream` / `_prepare_contexts` 添加 `@observe` 装饰器，`run_config` 透传给 `rewrite_query` / `answer_question` / `answer_with_messages`
- `app.py` lifespan finally 调用 `flush_langfuse` 避免异步队列丢失
- 自部署模板 `docker-compose.langfuse.yml`

### 用户反馈闭环

- 用户对答案点赞/点踩并记录到 DB，用于持续优化（阶段 10.2 后端 + 前端补充）
- `Feedback` ORM 模型：`request_id` 唯一主关联键 + 可空 `message_id` 外键（`ondelete=SET NULL`）+ `rating` 枚举（like/dislike）+ `comment`（最长 2000 字符）；`FeedbackRating` 枚举 + `FeedbackNotFoundError` 异常
- `FeedbackRepository`：`upsert` / `get_by_request_id` / `list`（按 rating / conversation_id 筛选）/ `delete`，只 `flush` 不 `commit`（事务由路由层显式控制）
- Alembic 迁移：创建 `feedback` 表
- API：`POST /api/v1/feedback`（Upsert，201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`（200 / 404）、`GET /api/v1/feedback?rating=&conversation_id=&limit=`（列表筛选）、`DELETE /api/v1/feedback/{request_id}`（204 / 404）
- Schemas：`FeedbackCreate` / `FeedbackRead` / `FeedbackList`（Pydantic v2）
- 路由直接调 Repository，不新建 `FeedbackService`（避免空模块）
- `request_id` 作为主关联键的决策与已知局限见 [ADR 0001](./adr/0001-request-id-as-feedback-key.md)（已被 [ADR 0003](./adr/0003-message-request-id.md) 部分取代：`request_id` 现持久化到 `Message` 表）
- 后端 30 个单元测试（Repository 12 + API 18，端到端验证）
- **前端补充**（PR #87 / Issue #86）：`ApiClient.submit_feedback` / `delete_feedback` 封装 POST/DELETE；`_render_feedback_buttons` 在 assistant 气泡内渲染点赞/点踩按钮，状态缓存在 `st.session_state`，Upsert 语义切换赞↔踩，再次点击撤销 DELETE，点踩后展开 `st.text_area` 收集文字评论；9 个新增单元测试（`TestSubmitFeedback` 6 + `TestDeleteFeedback` 3）
- **历史消息反馈 prefactor**（PR #93 / Issue #89）：`Message` 表新增可空 `request_id` 列（唯一索引）+ Alembic 迁移 `b8f2a3c4d5e6` + ADR 0001 标记 Superseded + ADR 0003 新写 + CONTEXT.md 术语微调；7 个新增单元测试（4 模型层 + 3 迁移层）
- **历史消息反馈写入读出**（PR #94 / Issue #90）：`_persist_turn` 加 `request_id` 必填参数，`answer` / `answer_stream` 两条路径透传到 assistant `Message`（user 消息不写）；`ConversationRepository.add_message` 签名加可选 `request_id: uuid.UUID | None = None`（保持 ORM 写入唯一入口约定）；`MessageRead` schema 加 `request_id: uuid.UUID | None`，`GET /api/v1/conversations/{id}/messages` 响应自动携带；5 个新增单元测试（写入路径 3 + 读出路径 1 + repository 签名 1）
- **历史消息反馈前端 model+client**（PR #95 / Issue #91）：`MessageInfo` dataclass 加 `request_id: str | None = None` 字段，`_parse_message` 从 API 响应 dict 解析 `request_id`（缺失时 None，兼容 user 消息与迁移前旧消息）；新增 `ApiClient.get_feedback(request_id) -> FeedbackInfo | None` 封装 `GET /api/v1/feedback/{request_id}`：200 返回 `FeedbackInfo`，404 返回 `None`（不抛异常，前端用 None 表示「未反馈」），其他 HTTP 错误仍抛 `ApiClientError`（仅 404 转 None，避免掩盖 401/500 等真实错误）；同步更新 `make_message_dict` helper 加 `request_id` 键；5 个新增单元测试（`MessageInfo.request_id` 解析 2 + `get_feedback` 三分支 3）
- **历史消息反馈前端 UI 渲染**（PR #96 / Issue #92）：`_render_feedback_buttons` 从「仅新消息」扩展到 `conversation_messages` 历史消息循环，历史会话中的 assistant 消息显示反馈按钮并正确初始化状态；旧消息（`request_id=None`）隐藏按钮（点击必然 404，体验差）；新增 `_init_feedback_state_for_history` 纯函数批量初始化反馈状态（进入历史会话时查询每条 assistant 消息的反馈状态写入 `session_state[f"feedback-{request_id}"]`，已有状态不覆盖避免 rerun 时丢失用户本会话刚操作的反馈）；提取 `_should_render_feedback_for_message` 与 `_feedback_state_key` 纯函数消除重复；修复 `_handle_question` 追加 assistant 消息时未传 `request_id` 的遗漏（否则 `st.rerun()` 后这条消息的 `request_id` 是 None，反馈按钮消失）；mypy strict 类型用 `cast("MutableMapping[str, object]", st.session_state)` 桥接 streamlit `SessionStateProxy` 与函数声明的接口类型；8 个新增单元测试（`_init_feedback_state_for_history` 五分支 5 + `_should_render_feedback_for_message` 三分支 3）；历史消息反馈端到端体验闭环完成

### 性能优化

- 检索阶段 P95 延迟优化（阶段 10.3）：BM25 索引缓存 + 并发检索（Qdrant 路径）
- `BM25IndexCache`：进程级缓存经 FastAPI 依赖注入到 `QaService`，签名 `tuple(sorted(doc_ids)) + total_chunks` 自动失效（文档增删/重新切分时自动重建），不耦合 `DocumentService`
- 并发检索：`_retrieve_hybrid` 内用 `concurrent.futures.ThreadPoolExecutor(max_workers=2)` 并行 BM25 与 Qdrant 检索（numpy 打分 + 网络 I/O 都释放 GIL）；InMemory 测试路径保持串行
- 基准脚本 `scripts/benchmark_retrieval.py`（走真实 `QaService` 路径，P50/P95/P99，冷热两遍）与 `scripts/verify_bm25_cache.py`（对比 `--no-cache` 基线与缓存路径）
- 验收结果：检索阶段 P95 从 1727.9ms 降至 408.7ms，降幅 76.3%（远超 ≥50% 标准）
- P95 验收口径（仅检索阶段，不含 LLM/reranker）与设计决策见 [ADR 0002](./adr/0002-retrieval-stage-p95-metric.md)

### Docker 部署

- Docker Compose 一键部署（阶段 11.4）：`docker compose up -d --build` 启动 API + Qdrant + PostgreSQL 三服务
- `Dockerfile`：`python:3.11-slim` + 多阶段复制 `uv` + `uv sync --frozen --extra embedding --extra chinese`；`libgomp1`（BM25/lightgbm）+ `curl`（healthcheck）
- `docker-compose.yml`：api / qdrant / postgres 三服务 + postgres healthcheck + 三个命名卷（`rrag-postgres-data` / `rrag-qdrant-data` / `rrag-api-uploads`）持久化
- `docker/entrypoint.sh`：先 `alembic upgrade head` 迁移 schema，再 `exec uvicorn` 启动 API（`exec` 接管 PID 1 优雅接收 SIGTERM）
- `pyproject.toml`：补 `psycopg[binary]>=3.1`（PostgreSQL 驱动，psycopg3 是 SQLAlchemy 2.0 推荐）
- 部署文档见 [README.md](../README.md)「Docker 部署」章节；Streamlit UI 不容器化，本地运行指向容器化 API

### 认证鉴权

- API Key 认证（阶段 11.1）：所有 `/api/v1/*` 端点接入认证，防止未授权访问
- 新增 `src/research_rag/api/auth.py`：`verify_api_key` 依赖函数，`HTTPBearer(auto_error=False)` 从 `Authorization: Bearer <key>` 提取 token，与 `API_KEYS` 环境变量配置的有效 key 集合比对
- `app.include_router(..., dependencies=[Depends(verify_api_key)])` 集中挂载，所有路由（documents / queries / conversations / feedback）自动生效，无需改每个路由文件
- 环境变量 `API_KEY_ENABLED` 控制开关（默认禁用，开发友好，向后兼容现有测试）；`API_KEYS` 逗号分隔配置多个有效 key
- `secrets.compare_digest` 恒定时间比对防时序攻击；启用但 `API_KEYS` 为空时安全失败（全部 401，避免认证形同虚设）
- Streamlit `ApiClient` 从 `API_KEY` 环境变量读 key，`_get_headers()` 在所有 HTTP 调用（`_request` + `ask_question_stream`）携带 `Authorization: Bearer <key>`；未设置时不携带（兼容禁用认证场景）
- 31 个新增单元测试（纯函数 + 集成路径 + ApiClient），现有 620+ 测试零改动
- 不在范围：用户注册/登录系统 + JWT（后续独立 Issue）、权限分级、API Key 签发/轮换管理界面

### 输入过滤与文件校验

- 输入校验安全模块（阶段 11.2）：PDF 白名单 + 文件大小限制 + Prompt 注入过滤，防止恶意文件上传与注入攻击
- 新增 `src/research_rag/api/security.py`：与 `api/auth.py` 对称，集中管理输入校验逻辑
- **文件类型校验**：扩展名 + `content_type` 双重白名单（仅允许 PDF），非 PDF 返回 415；`content_type` 缺失时只校验扩展名（兼容 Streamlit 等不设置 content_type 的客户端）
- **文件大小校验**：`MAX_UPLOAD_MB` 环境变量（默认 20MB，复用已有变量），超过返回 413（`HTTP_413_CONTENT_TOO_LARGE`）
- **Prompt 注入过滤**：10 类常见注入模式正则匹配（`ignore previous` / `disregard` / `you are` / `act as` / `system:` / `<|im_start|>` / `[/inst]` / `reveal instructions` / `jailbreak` / `DAN`），命中即返回 400（不净化后放行，避免语义漂移）
- 路由层调用：`documents.py` 调 `validate_upload_file`、`queries.py` 调 `validate_question`（在 stream 分支前完成，覆盖流式/非流式两条路径）
- 环境变量 `INPUT_VALIDATION_ENABLED` 控制开关（**默认启用**，与 11.1 认证默认禁用相反——安全功能默认开是最佳实践，且校验只影响非法请求不影响合法用户）
- 77 个新增单元测试（纯函数 + 路由集成），现有 650+ 测试零改动
- 不在范围：文件内容深度校验（PDF 内嵌 JS/病毒扫描）、复杂 Prompt 注入防御（LLM 检测）、XSS 过滤（Streamlit 已转义）

### API 限流

- API 请求限流（阶段 11.3）：`slowapi` 库按用户/IP 限制请求频率，超频返回 429，防止滥用消耗 LLM/检索资源
- 新增 `src/research_rag/api/rate_limit.py`：`Limiter` 模块级单例 + `default_limits` callable lambda（请求时动态读环境变量，支持 monkeypatch 测试）
- `rate_limit_key` 函数：认证启用按 `key:<token>`（公司出口 IP 共享，按 key 更精确），认证禁用按 `ip:<ip>`（X-Forwarded-For 首段或 client.host）
- `app.py` 集中挂载：`configure_limiter()` + `app.state.limiter` + `SlowAPIMiddleware` + `RateLimitExceeded` 异常处理器（返回 `ErrorResponse` JSON 体 + `Retry-After` / `X-RateLimit-*` 头）
- 上传端点 `POST /api/v1/documents` 单独更严限流（`@limiter.limit` 装饰器，默认 10/min vs 全局 60/min），避免上传刷接口拖垮服务
- 环境变量：`RATE_LIMIT_ENABLED`（开关，默认 false 与 11.1 一致保护现有测试）、`RATE_LIMIT_PER_MINUTE`（默认 60）、`RATE_LIMIT_UPLOAD_PER_MINUTE`（默认 10）
- **FastAPI 0.139+ 兼容 patch**：`_patch_find_route_handler` 替换 `slowapi.middleware._find_route_handler`，深入 `_IncludedRouter.original_router.routes` 找 endpoint（未打 patch 时 `default_limits` 对所有 `/api/v1/*` 端点失效）
- 45 个新增单元测试（纯函数 + 路由集成 + FastAPI 0.139+ 兼容性），现有 720+ 测试零改动
- 不在范围：Redis 后端（多副本时切换 `storage_uri`）、滑动窗口（slowapi 默认固定窗口，当前规模可接受）

### CI/CD 自动化部署

- CI/CD 自动化部署（阶段 11.5）：push 到 main 后（CI 全绿后）自动构建 Docker 镜像并推送到 GitHub Container Registry，可选 SSH 自动部署到生产服务器
- 新增 `.github/workflows/deploy.yml`：独立 CD workflow（与 `ci.yml` 职责分离），`workflow_run`（CI 成功后自动）+ `workflow_dispatch`（手动）触发
- `build-and-push` job：`docker/build-push-action@v6` 构建并推送镜像到 `ghcr.io/fufufu11/research-rag-assistant`，双标签 `:latest` + `:sha-<short>`，GHA buildx 缓存加速
- `deploy` job：`appleboy/ssh-action@v1.2.0` SSH 登录服务器 `docker compose pull && up -d` + 健康检查（12 次 × 5 秒）；门控于 `vars.ENABLE_SSH_DEPLOY == 'true'`（未配置时只构建不部署，本地开发友好）
- 新增 `docker-compose.prod.yml`：生产覆盖文件，`api` 服务引用 GHCR 预构建镜像（`${IMAGE_TAG:-latest}` 支持版本回滚），复用基础 `docker-compose.yml` 的 postgres/qdrant/volumes 配置
- 新增 `tests/unit/test_deployment_config.py`：15 个测试验证 workflow 与 compose 配置的 YAML 语法与关键结构（权限 / 步骤 / 依赖 / 门控 / 镜像引用 / 健康检查脚本）
- `pyproject.toml`：添加 `pyyaml>=6.0` dev 依赖；`README.md`：新增「CI/CD 自动化部署」章节（流水线 / 镜像拉取 / Secrets 与 Variables 配置 / 手动触发）
- 不在范围：蓝绿部署 / 金丝雀发布、Kubernetes 部署、回滚自动化（手动 `IMAGE_TAG=sha-xxx` 即可）、多环境（staging/prod）分离

### 生产安全加固（阶段 11.6 已完成）

- 阶段 11.6 生产安全加固：把开发级容器部署升级到生产可用——非 root 用户 + docker secrets 文件挂载 + Nginx TLS 反代终止 HTTPS
- **切片 A #97（已完成，PR #103）**：`src/research_rag/secrets.py` 提供 `get_secret(name) -> str | None` helper
  - 优先读 `{NAME}_FILE` 环境变量指向的文件内容（docker secrets 路径，如 `LLM_API_KEY_FILE=/run/secrets/llm_api_key`）
  - 无 `_FILE` 或文件不存在时回退到 `{NAME}` 环境变量（开发/CI 路径）
  - 两者均无返回 `None`；调用方用 `get_secret(NAME) or ""` 模式取值
  - 7 个单元测试覆盖五条行为路径（fallback env / file 优先 / file 缺失回退 / 空文件 / 仅空白文件）
- **切片 C #99（已完成，PR #104）**：5 个文件 8 个密钥读取点替换为 `get_secret` + postgres `POSTGRES_PASSWORD_FILE` 支持
  - **替换的密钥读取点**（5 个文件 8 处）：
    - `observability.py`：`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
    - `api/dependencies.py`：`LLM_API_KEY` / `DASHSCOPE_API_KEY` / `JINA_API_KEY`（`get_llm_config` + `get_embedding_config`）
    - `answer_evaluation.py`：`JUDGE_LLM_API_KEY` + fallback `LLM_API_KEY`（双层 fallback：judge 优先 → 主 LLM → 空）
    - `api/auth.py`：`API_KEYS`（多 key 逗号分隔，`_get_valid_api_keys` 用 `get_secret` 读取）
    - `embedding.py`：`DASHSCOPE_API_KEY` / `JINA_API_KEY`（`_create_api_embeddings` 在 `config.api_key` 为空时 fallback 读取）
  - **docker-compose.yml postgres 服务**：`environment` 新增 `POSTGRES_PASSWORD_FILE: ${POSTGRES_PASSWORD_FILE:-}`，Postgres 官方镜像原生支持 `_FILE` 后缀（设置后优先读文件内容作为密码，忽略 `POSTGRES_PASSWORD`）；未设置时为空字符串，镜像忽略 `_FILE` 后缀回退到 `POSTGRES_PASSWORD`（开发/CI 默认路径）；生产环境设置 `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password` 即可启用 docker secrets（切片 D #101 配置）
  - **测试覆盖**（7 个文件 +24 测试，837→854）：每个密钥读取点都有 `_FILE` 优先 / fallback env / 缺失返回 None 三条路径的单元测试；`test_deployment_config.py` 新增 `TestPostgresPasswordFile` 3 测试验证 compose 字段声明
  - **向后兼容**：所有调用点保留 fallback env 行为，开发/CI 不挂载 secrets 时行为不变；830 基线零回归
- **切片 B #98（已完成，PR #105）**：api 容器非 root 改造
  - `Dockerfile` 加 `RUN groupadd -r app && useradd -r -g app -u 65532 app && chown -R app:app /app`（创建 UID 65532 的 app 用户 + chown /app 让非 root 用户可读 `.venv`/源码 + 可写 `data/uploads`，必须在所有 COPY 之后执行确保 entrypoint.sh 也被 chown）
  - `Dockerfile` 加 `USER 65532`（在 chown 之后，否则 chown 以非 root 执行会失败）
  - `docker/entrypoint.sh` 从 `uv run uvicorn` / `uv run alembic` 改为 `/app/.venv/bin/uvicorn` / `/app/.venv/bin/alembic` 直接调用（非 root 用户无权写 `~/.cache/uv`，`uv run` 会因 cache 写入失败而崩溃，直接调用 venv 内可执行文件绕开）
  - 保留 `exec` 让 uvicorn 接管 PID 1，`docker stop` 能优雅关闭（SIGTERM 直接给 uvicorn 而非 sh）
  - 7 个新增单元测试（`tests/unit/test_dockerfile_non_root.py`）：`TestDockerfileNonRoot`（4 个：USER 65532 / groupadd+useradd / chown / 顺序约束）+ `TestEntrypointScript`（3 个：venv uvicorn / venv alembic / exec 保留）
  - 容器行为测试（`id` 命令 / 健康检查 / uploads 可写）需真实 Docker 构建后验证，本地 pytest 只验证文件结构与关键指令存在性
- **切片 D #101（已完成，PR #106）**：docker-compose.prod.yml 配置 8 个 docker secrets 挂载
  - 顶级 `secrets:` 块声明 8 个 secrets（`postgres_password` / `llm_api_key` / `judge_llm_api_key` / `api_keys` / `langfuse_public_key` / `langfuse_secret_key` / `dashscope_api_key` / `jina_api_key`），每个用 `file: ${VAR_FILE:?must point to host file path}` 指向宿主机路径（未设置时 `:?` 报错引导运维配置）
  - `api` 服务 `secrets:` 引用 7 个 secrets（除 `postgres_password`）+ `environment` 加 7 个 `{NAME}_FILE` 指向 `/run/secrets/<name>`，应用层 `get_secret` helper 优先读 `_FILE` 文件内容
  - `postgres` 服务 `secrets:` 引用 `postgres_password` + `POSTGRES_PASSWORD_FILE` 覆盖为 `/run/secrets/postgres_password`（Postgres 官方镜像读取文件内容作为密码）
  - 新增 `.env.docker.secrets.example` 示例文件（8 个 secrets 文件宿主机路径占位，运维复制为 `.env.docker.secrets` 后填入真实路径）；`.gitignore` 加 `.env.docker.secrets`（真实路径文件不入 git，`.example` 可提交）
  - 10 个新增单元测试（`tests/unit/test_deployment_config.py`）：`TestProdComposeDockerSecrets`（8 个：顶级 secrets 块 / 8 secrets 定义 / api 引用 7 / postgres 引用 password / POSTGRES_PASSWORD_FILE 路径 / api environment 7 个 _FILE / 服务引用一致性 / env 路径与 secret 名一致性）+ `TestDockerSecretsEnvExample`（2 个：example 文件存在 + 含 8 个 _FILE 变量 + 路径非空）
  - 已知局限：`api` 服务的 `DATABASE_URL` 仍用 base compose 的 `${POSTGRES_PASSWORD:-rrag}` 模式（生产环境需通过 entrypoint 脚本读 `POSTGRES_PASSWORD_FILE` 内容动态拼装，留待后续 issue 处理）
- **切片 E #100（已完成，PR #107）**：nginx + certbot 容器化 + TLS 反代（Let's Encrypt webroot 模式）
  - 新增 `nginx/nginx.conf` 模板：HTTP server block（监听 80，`/.well-known/acme-challenge/` webroot + HTTP→HTTPS 301 重定向）+ HTTPS server block（监听 443，SSL 证书路径 + `proxy_pass http://api:8000` + 标准反代头 Host/X-Real-IP/X-Forwarded-For/X-Forwarded-Proto），用 `${DOMAIN}` 占位由 entrypoint `envsubst` 替换
  - 新增 `docker/nginx/entrypoint.sh`：`envsubst` 替换 `${DOMAIN}` 占位 → 若 `fullchain.pem` 不存在则 `openssl req -x509` 生成 1 天占位自签证书（让 nginx SSL server block 能加载配置启动）→ `crond` 周期 reload nginx（每 6 小时读取 certbot 续期后的新证书）→ `nginx -g 'daemon off;'` 前台运行
  - 新增 `docker/certbot/entrypoint.sh`：等待 nginx 启动 → 首次签发 `certbot certonly --webroot --webroot-path=/var/www/certbot -d ${DOMAIN} -m ${LETSENCRYPT_EMAIL} --agree-tos --no-eff-email --non-interactive` → `crond` 周期 `certbot renew`（每 12 小时检查，仅临近过期时实际续期）→ `crond -f` 前台运行
  - `docker-compose.prod.yml` 新增 nginx 服务（`nginx:alpine` + 80/443 端口 + nginx.conf/entrypoint/webroot/证书卷挂载 + `${DOMAIN}` env）+ certbot 服务（`certbot/certbot` + webroot/证书卷挂载 + `${DOMAIN}`/`${LETSENCRYPT_EMAIL}` env）+ 共享卷 `certbot-webroot` / `certbot-certs`
  - api 服务用 `ports: !reset []` 清空 base compose 的 8000 端口发布（compose merge 对 ports 取并集，需显式清空；生产仅 nginx 可访问 api，强制走 TLS）
  - 33 个新增单元测试（`tests/unit/test_deployment_config.py`）：扩展 `_load_yaml` 支持 compose-spec 的 `!reset`/`!override` 自定义 YAML 标签 + `TestNginxConfig`（8 个 nginx.conf 结构断言）+ `TestProdComposeNginxCertbot`（15 个 compose 服务/卷/端口断言）+ `TestNginxEntrypoint`（5 个 nginx entrypoint 结构断言）+ `TestCertbotEntrypoint`（5 个 certbot entrypoint 结构断言）
  - 设计决策：webroot 模式（不停服签发，standalone 需停 nginx）+ nginx 独立 reload cron（避免跨容器信号通信复杂度，6 小时 reload 足够及时）+ 占位自签证书（解决首次启动无证书 nginx 无法启动的鸡生蛋问题）
  - 不在范围：实际证书签发（部署运维文档说明，需真实域名）/ 泛域名 + dns-01 / HTTP/2 或 HTTP/3 / nginx 容器非 root 化
- **切片 F #102（已完成，PR #108）**：文档同步收官——ROADMAP.md 标记阶段 11.6 ✅ 已完成 + 新增三个子方向（密钥管理升级 / 非 root 容器 / Nginx TLS 反代）总结 + 设计取舍（UID 65532 / webroot 模式 / docker secrets 方案 A）汇总 + 验收结果（6 个切片 CI 全绿 + 830→905 测试 +75 新增零回归）汇总；STATUS.md 标记阶段 11.6 完成 + 版本 v2.9 → v3.0；本切片为纯文档变更，无新增测试，905 测试基线零回归
- 决策与设计取舍见 [ADR 0004](./adr/0004-docker-secrets-helper.md)：方案 A（代码层 helper）而非方案 B（pydantic-settings BaseSettings），YAGNI；postgres 用原生 `POSTGRES_PASSWORD_FILE` 而非自定义 entrypoint
- 不在范围：Vault / SOPS / Sealed Secrets 等外部密钥管理系统（YAGNI）、双向 TLS（mTLS）、WAF

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · LangChain · Qdrant · SQLAlchemy 2 + Alembic · React 18 + TypeScript + Vite · Vitest · pytest · Ruff + mypy · GitHub Actions · Langfuse · Docker Compose

## 本地运行

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 代码质量检查
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest

# PDF 解析 CLI
uv run python scripts/parse_pdf.py <pdf_path>

# 数据库迁移（首次运行或拉取新代码后执行）
uv run alembic upgrade head

# 启动 FastAPI API 服务（端口 8000）
uv run uvicorn research_rag.api.app:create_app --factory --reload --port 8000

# 启动 React SPA 前端开发服务器（端口 5173，需先启动 API 服务）
cd frontend
npm install      # 首次或修改 package.json 后
npm run dev      # vite dev server，API 请求 proxy 到后端 8000

# 前端测试 / 类型检查 / 构建（在 frontend/ 目录下）
npm run test     # vitest run
npm run lint     # tsc --noEmit
npm run build    # tsc -b && vite build

# 运行检索评测
uv sync --extra embedding
uv run python scripts/evaluate.py verify --pdfs-dir <含 PDF 的目录>
uv run python scripts/evaluate.py run --pdfs-dir <含 PDF 的目录>
```

## 测试状态

- pytest：829 passed（T1 删除 Streamlit UI 层后端减 109 个 UI/ApiClient 测试，详见 ADR 0005；阶段 9.1 新增 16 个、阶段 9.2 新增 111 个、阶段 9.3 新增 65 个、阶段 10.1 新增 25 个、阶段 10.2 后端新增 30 个、阶段 10.2 前端补充新增 9 个、阶段 11.1 新增 31 个、阶段 11.2 新增 77 个、阶段 11.3 新增 45 个、阶段 11.5 新增 15 个、#89 历史消息反馈 prefactor 新增 7 个、#90 历史消息反馈写入读出新增 5 个、#91 历史消息反馈前端 model+client 新增 5 个、#92 历史消息反馈前端 UI 渲染新增 8 个、#97 secrets.py helper 新增 7 个、#99 密钥读取点替换 + postgres 密码文件支持新增 24 个、#98 非 root 容器 Dockerfile 改造新增 7 个、#101 docker-compose.prod.yml docker secrets 配置新增 10 个、#100 nginx + certbot 容器化 + TLS 反代新增 33 个、#102 文档同步收官纯文档无新增、UI 体验优化阶段二新增 33 个：#109 左侧导航重构 +6 / #112 输入栏上传按钮+免责声明 +8 / #111 对话区居中布局 +5 / #113 AI 回复复制按钮 +8 / #110 顶部模型下拉占位 +6）
- vitest：33 passed（T1 9 个 ApiClient/App 测试 + T2 新增 24 个：App 3 + Sidebar 6 + ChatArea 4 + ModelDropdown 4 + claude-theme 9）
- ruff format --check：通过
- ruff check：通过
- mypy：CI 环境（Linux）通过；本机 Windows 因应用程序控制策略阻止 C 扩展加载无法运行

## 已知问题

1. **mypy 本机不可用**：uv 管理的独立 Python 的 C 扩展在 Windows 本机被应用程序控制策略阻止加载。CI 环境（Linux）使用系统 Python，不受影响。已创建 conda 环境 `rrag311`（Python 3.11.15）作为本地替代，但 mypy 仍受 `_sqlite3.dll` 阻塞无法运行，留待 CI 验证。
2. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
3. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。
4. **本地 pytest 全量运行注意事项**：部分测试可能加载真实 Embedding 模型导致卡住，建议单独跑改动相关测试文件；API 路由测试需设置 `$env:RERANKER_ENABLED="false"; $env:QDRANT_ENABLED="false"` 避免 lifespan 加载真实 reranker/Qdrant。

## 后续可选方向

- 历史消息反馈（阶段 10.2 已识别局限）：后端 `Message` 模型新增 `request_id` 字段 + 前端 `MessageInfo` 同步，让历史消息也能点赞/点踩—— **prefactor 已完成**（PR #93 / Issue #89：`Message.request_id` 列 + ADR 0003）；**写入读出已完成**（PR #94 / Issue #90：`_persist_turn` 透传 `request_id` + `MessageRead` schema 暴露 `request_id` + `add_message` 签名扩展）；**前端 model+client 已完成**（PR #95 / Issue #91：`MessageInfo.request_id` + `ApiClient.get_feedback` 404 转 None）；**前端 UI 渲染已完成**（PR #96 / Issue #92：`_render_feedback_buttons` 扩展到历史消息 + `_init_feedback_state_for_history` 批量初始化反馈状态 + 旧消息隐藏按钮）；历史消息反馈端到端体验闭环完成
- UI 体验优化阶段二（#109-#113）：基于 ChatGPT 界面截图的二轮迭代—— **已完成**（左侧导航重构 #109 + 输入栏上传按钮+免责声明 #112 + 对话区居中布局 #111 + AI 回复复制按钮 #113 + 顶部模型下拉占位 #110，5 个 ticket 全部 squash 合并到 main，905 → 938 测试基线零回归，+33 新增）
- 顶部模型选择下拉真切换（占位升级）：需后端补 `/api/v1/config` 端点返回可用模型列表，前端 `_render_model_dropdown` 移除 `disabled=True` 后接入
- 用户注册登录系统 + JWT（11.1 已预留 HTTPBearer 格式兼容，切换成本低）
- 表格感知切分与公式识别（阶段 12）
