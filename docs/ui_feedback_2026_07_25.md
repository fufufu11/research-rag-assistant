# UI 试用反馈与改进清单（2026-07-25）

> 用户首次从终端用户视角试用系统后提出的反馈。本文档用于交接给下一个 AI
> 作为改进任务的工作清单。每条问题含：现象、根因（已查明的标注）、
> 建议方向、相关文件。

## 试用环境

- 部署方式：本地 `uv sync` + Qdrant 单容器（`docker run` 启动的 `rrag-qdrant`）
- LLM：硅基流动 `deepseek-ai/DeepSeek-V3.2`
- Embedding：`BAAI/bge-small-zh-v1.5`（CPU 推理，torch 2.13.0+cpu）
- Reranker：关闭（避免下载 1GB 模型，基础检索已可用）
- BM25 混合检索：开启（jieba 中文分词）
- 数据库：SQLite（`data/app.db`）
- 启动命令：
  - Qdrant：`docker start rrag-qdrant`（已创建的容器，数据卷 `rrag-qdrant-data` 持久化）
  - API：`uv run uvicorn research_rag.api.app:create_app --factory --host 0.0.0.0 --port 8000`（需先 `Get-Content .env | ...` 加载环境变量）
  - UI：`uv run streamlit run src/research_rag/ui/app.py --server.port 8501`（设 `$env:API_BASE_URL="http://localhost:8000/api/v1"`）

## 问题 1：部署方式与数据持久化（已查明，无需改代码）

### 用户问题

> 当前还是用的 Docker 部署吗？Qdrant 容器是什么？网页的数据是存到我本地的吗？
> 比如我上传了一篇 PDF 之后，我下次打开的话，它还会存在。

### 答案

- **当前部署方式**：不是 Docker Compose 一键部署（11.4 已交付但试用时因 CUDA 包过大 2.5GB 改用本地 uv sync）。当前是**混合部署**：
  - Qdrant 用 Docker 单容器跑（`rrag-qdrant`，镜像 270MB，数据卷 `rrag-qdrant-data` 持久化）
  - FastAPI + Streamlit 本地 `uv run` 启动（CPU-only torch，约 270MB）
- **Qdrant 容器是什么**：Qdrant 是开源向量数据库，存 PDF 切分后的 chunk 向量。容器是它的运行实例，通过 `http://localhost:6333` 访问。
- **数据持久化**：是的，重启后 PDF 还在。三个持久化点：
  1. SQLite `data/app.db`：文档元数据、会话、反馈（重启不丢）
  2. Qdrant 数据卷 `rrag-qdrant-data`：向量数据（容器 `docker stop` 不丢，只有 `docker rm -v` 才删）
  3. 上传的 PDF 文件 `data/uploads/`：原始文件（重启不丢）
- **下次打开流程**：启动 Qdrant 容器 + API + UI，之前的文档和会话都还在。

## 问题 2：UI 界面改造（核心改进任务）

### 用户问题

> 我觉得这个 UI 界面做的不太好，我想把它做成那种比较标准的 AI 问答界面，
> 比如左侧是可以记录历史对话。右侧的话就是一个标准的问答框。

### 现状

当前 UI 是单栏垂直堆叠（Streamlit 默认布局）：
- 顶部：标题
- 中部：文档管理（上传 + 列表 + 删除）
- 下部：会话管理 + 问答输入框 + 答案展示

文件：[src/research_rag/ui/app.py](../src/research_rag/ui/app.py)

### 建议方向

参考 ChatGPT / Claude / DeepSeek Web 界面的标准布局：

```
┌─────────────────┬──────────────────────────────────┐
│  左侧栏 (25%)    │  右侧主区 (75%)                   │
│                 │                                  │
│  + 新建对话      │  ┌──────────────────────────┐   │
│                 │  │  消息流（user/assistant   │   │
│  📚 历史对话     │  │   交替，assistant 含引用）│   │
│  - 对话 1       │  │                          │   │
│  - 对话 2 (当前) │  │                          │   │
│  - 对话 3       │  └──────────────────────────┘   │
│                 │                                  │
│  📄 文档管理     │  ┌──────────────────┐ [发送]    │
│  - paper1.pdf ✅│  │ 输入问题...       │           │
│  - paper2.pdf ✅│  └──────────────────┘           │
│  [上传 PDF]     │                                  │
└─────────────────┴──────────────────────────────────┘
```

### 关键实现点

- Streamlit 用 `st.columns([1, 3])` 或 `st.sidebar` 实现左右分栏
- 左侧：会话列表（点击切换）+ 文档管理（折叠或底部）
- 右侧：消息流（`st.chat_message` 组件，user/assistant 区分）+ 底部输入框（`st.chat_input` 组件，回车自动发送，自动清空）
- 用 `st.chat_message` + `st.chat_input` 是 Streamlit 1.40+ 原生支持的聊天界面组件，能解决「问题 3 提问后没衔接口」的问题
- 流式输出用 `st.write_stream` 渲染到 `st.chat_message("assistant")` 内
- 引用卡片渲染在 assistant 消息下方

### 相关文件

- 主要改：[src/research_rag/ui/app.py](../src/research_rag/ui/app.py)
- 可能调：[src/research_rag/ui/api_client.py](../src/research_rag/ui/api_client.py)（如需补充 API 调用）

## 问题 3：多轮对话衔接（核心改进任务，UI 层问题）

### 用户问题

> 好像现在只能支持一轮的对话。当我输入问题并回车之后，答案只会出现在下面。
> 然后并没有一个让我重新输入新的提问的一个衔接方式。

### 现状

当前用 `st.text_area` + `st.button("提问")`：
- 提问后答案渲染在按钮下方
- 但 text_area 内容不清空，用户不知道该清空再问还是继续输入
- 历史消息和新一轮提问视觉上没分隔

后端**已经支持多轮**（阶段 9.2 已交付：会话持久化 + 查询改写 + 历史截断），问题是 UI 没暴露好。

### 建议方向

改用 `st.chat_input`：
- 回车自动发送，发送后自动清空输入框
- 消息以聊天气泡形式追加到上方消息流
- 视觉上每轮 user/assistant 明确分隔
- 配合 `st.chat_message` 组件天然支持多轮视觉

### 相关文件

- 主要改：[src/research_rag/ui/app.py](../src/research_rag/ui/app.py) 的 `_render_qa` 函数

## 问题 4：多文档会话只检索到一篇（核心改进任务，疑似 Bug）

### 用户问题

> 我尝试一下子上传两篇文档，然后传入会话中，但是最终 AI 分析的时候，
> 似乎只能看到一篇文档。

### 根因（已查明）

UI 的「新建会话」按钮调用 `client.create_conversation()` **没传 `document_ids`**，会话创建时 `document_ids=None`（表示"全库"）。

但用户在 UI 上选了两篇文档后提问时，UI 把 `selected_ids` 传给 `ask_question_stream`。此时后端 `QaService.answer_stream` 的行为：

```python
# src/research_rag/services/qa_service.py line 276-277
if conv.document_ids is not None:
    effective_doc_ids = [uuid.UUID(d) for d in conv.document_ids]
```

由于 `conv.document_ids is None`，**不会**覆盖请求传入值，`effective_doc_ids = document_ids`（用户选的两篇）。所以**理论上应该检索两篇**。

实际只看到一篇的可能原因（需新 AI 排查）：
1. **检索 top_k=8**：两篇文档各召回 4 个 chunk，但 RRF 融合后可能 8 个都来自同一篇（如果那篇相关性更高）
2. **LLM prompt 构造**：context 里有两篇内容，但 LLM 答案只引用了一篇的 chunk（引用编号 `[C1]` 只指向一篇）
3. **UI 渲染问题**：API 返回了多个文档的引用，但 UI 只渲染了第一个

### 建议排查步骤

1. 上传两篇文档后，提问"这两篇论文分别讲了什么？"
2. 查 API 日志（`api-server.log`）：看 `effective_doc_ids` 是几个 UUID
3. 看返回的 `citations` 列表：`document_id` 字段是否含两个不同 UUID
4. 如果 citations 只有一个 UUID，是检索问题（top_k 分配不均）；如果 citations 有两个但 UI 只显示一个，是 UI 渲染问题

### 建议改进方向

- **UI 新建会话时让用户选文档范围**：`client.create_conversation(document_ids=selected_ids)`，让会话锁定范围与用户选择一致
- **检索层面**：考虑按文档分组检索（每篇至少召回 N 个 chunk），避免一篇文档占满 top_k
- **UI 引用展示**：明确标注每个引用来自哪篇文档（`[C1] paper1.pdf p.3` vs `[C2] paper2.pdf p.5`）

### 相关文件

- UI：[src/research_rag/ui/app.py](../src/research_rag/ui/app.py) 的 `_render_conversation_management` 和 `_render_qa`
- 后端检索：[src/research_rag/services/qa_service.py](../src/research_rag/services/qa_service.py) 的 `answer` / `answer_stream`
- 混合检索：[src/research_rag/hybrid_retriever.py](../src/research_rag/hybrid_retriever.py)
- API 路由：[src/research_rag/api/routes/queries.py](../src/research_rag/api/routes/queries.py)

## 改进任务优先级建议

| 优先级 | 任务 | 理由 |
|---|---|---|
| P0 | 问题 2 + 3 合并：UI 改造为 ChatGPT 风格 | 用户体验最大瓶颈，后端已就绪只差 UI |
| P1 | 问题 4：多文档检索 Bug 排查与修复 | 影响核心功能正确性 |
| P2 | 问题 1：补充部署文档（混合部署方式的说明） | 文档完善，不阻塞试用 |

## 验收标准（建议）

- [ ] UI 改造后：左侧会话列表 + 右侧聊天框，回车发送自动清空，多轮对话视觉清晰
- [ ] 多文档场景：上传 2+ 篇 PDF 后提问"分别讲了什么"，答案引用包含所有上传文档
- [ ] 重启服务后：会话历史和文档列表完整保留
