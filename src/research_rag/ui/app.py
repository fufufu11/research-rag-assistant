"""Streamlit 演示界面入口。

依据 PROJECT_PLAN.md 第 716-722 行（阶段 6 验收：浏览器完成完整流程）、
第 13.6 节（UI 层只调 API，不直接 import 业务层）、阶段 9.2 多轮对话。

启动方式（两个服务分开运行）：
1. 先启动 FastAPI API 服务（端口 8000）：
   ``uv run python scripts/run_server.py``
2. 再启动 Streamlit 界面（端口 8501）：
   ``uv run streamlit run src/research_rag/ui/app.py``

设计取舍（初学者向说明）：
- **UI 层只调 HTTP API**：本界面通过 ``ApiClient`` 调用 FastAPI，不直接
  import ``DocumentService`` / ``QaService``。所有业务逻辑（PDF 解析、
  向量检索、LLM 问答）都在 API 服务端完成，UI 只负责展示和交互
  （PROJECT_PLAN 第 13.6 节分层）。
- **界面分三块**：文档管理（上传/列表/删除）、会话管理（新建/切换/删除，
  阶段 9.2）、问答（输入框 + 带引用展示 + 历史消息回看）。对齐验收标准
  "上传 PDF → 查看文档列表 → 提问并查看带引用的答案 → 删除文档" + 阶段 9.2
  "连续 3 轮对话内能正确理解指代"。
- **错误用 ``st.error`` 展示**：``ApiClientError`` 捕获后直接展示 detail，
  不阻断整个界面（其他功能区仍可用）。
- **``st.session_state`` 缓存文档列表**：上传/删除后刷新缓存，避免每次
  重新请求。问答结果也存 session_state，便于引用展开时回看。
- **会话状态管理（阶段 9.2）**：``st.session_state["current_conversation_id"]``
  存当前会话 ID，``st.session_state["conversation_messages"]`` 存当前会话的
  历史消息列表（切换会话时从 API 重新拉取）。新建会话按钮清空当前会话状态，
  切换会话按钮拉取目标会话历史。
- **不加载 .env**：Streamlit 只需 ``API_BASE_URL``（有默认值），LLM 密钥等
  由 API 服务端（``run_server.py``）加载。UI 不接触敏感配置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from research_rag.ui.api_client import (
    ApiClient,
    ApiClientError,
    ConversationInfo,
    DocumentInfo,
    MessageInfo,
    QueryResult,
    StreamDone,
    StreamError,
    StreamToken,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# 缓存 ApiClient 实例，避免每次 rerun 重建
_CLIENT_KEY = "_api_client"


def _get_client() -> ApiClient:
    """获取缓存的 ``ApiClient`` 实例。"""

    if _CLIENT_KEY not in st.session_state:
        st.session_state[_CLIENT_KEY] = ApiClient()
    client: ApiClient = st.session_state[_CLIENT_KEY]
    return client


def _refresh_documents() -> None:
    """清除 session_state 中的文档列表缓存，触发下次重新拉取。"""

    st.session_state.pop("documents", None)


def _render_header() -> None:
    """渲染页面标题和说明。"""

    st.set_page_config(page_title="科研文献智能问答", page_icon="📚", layout="wide")
    st.title("📚 科研文献可溯源智能问答系统")
    st.caption("上传 PDF → 提问 → 获取带引用的答案。所有处理由 FastAPI 后端完成。")


def _render_document_management(client: ApiClient) -> None:
    """渲染文档管理区：上传 / 列表 / 删除。"""

    st.header("📄 文档管理")

    # --- 上传区 ---
    uploaded = st.file_uploader(
        "上传 PDF 文档",
        type=["pdf"],
        help="选择 PDF 文件，上传后将自动解析、切分并建立向量索引。",
    )
    if uploaded is not None and st.button("确认上传", type="primary"):
        try:
            with st.spinner("正在上传并解析文档…"):
                doc = client.upload_document(uploaded.getvalue(), uploaded.name)
            st.success(f"上传成功：{doc.original_name}（{doc.page_count} 页，状态：{doc.status}）")
            _refresh_documents()
        except ApiClientError as exc:
            st.error(f"上传失败：{exc.detail}")


def _render_document_list(client: ApiClient) -> None:
    """渲染文档列表和删除按钮。"""

    st.subheader("文档列表")

    if "documents" not in st.session_state:
        try:
            st.session_state["documents"] = client.list_documents()
        except ApiClientError as exc:
            st.error(f"无法获取文档列表：{exc.detail}")
            return

    docs: list[DocumentInfo] = st.session_state["documents"]
    if not docs:
        st.info("暂无文档，请先上传 PDF。")
        return

    for doc in docs:
        with st.container(border=True):
            col1, col2, col3 = st.columns([5, 3, 1])
            with col1:
                st.write(f"**{doc.original_name}**")
                st.caption(f"ID: `{doc.id}`")
            with col2:
                status_label = {
                    "ready": "✅ 就绪",
                    "pending": "⏳ 处理中",
                    "failed": "❌ 失败",
                }.get(doc.status, doc.status)
                st.write(f"状态：{status_label}")
                st.caption(f"{doc.page_count} 页 | 创建于 {doc.created_at}")
            with col3:
                if doc.error_message:
                    st.caption("⚠️ 有错误")
                    st.caption(doc.error_message)
                if st.button("删除", key=f"del-{doc.id}", help="删除文档及其向量"):
                    try:
                        client.delete_document(doc.id)
                        st.success(f"已删除：{doc.original_name}")
                        _refresh_documents()
                        st.rerun()
                    except ApiClientError as exc:
                        st.error(f"删除失败：{exc.detail}")


def _render_qa(client: ApiClient) -> None:
    """渲染问答区：会话管理 + 历史消息 + 输入框 + 流式答案 + 引用展示。

    阶段 9.1 起默认走流式路径（``ask_question_stream`` + ``st.write_stream``）：
    用户点击「提问」后，LLM 生成内容逐字渲染到界面；流结束后由 ``done`` 事件
    携带的引用元数据补充渲染引用详情。问答结果存入 ``session_state``，后续
    rerun（无按钮点击）时由 ``_render_query_result`` 从存储结果完整重渲染。

    阶段 9.2 起支持多轮对话：顶部渲染会话管理区（新建/切换/删除），中间渲染
    当前会话的历史消息（user/assistant 交替），底部是问答输入框。提问时若
    已选中会话则传 ``conversation_id``，API 端加载历史注入 prompt 并持久化
    本轮消息；流式 done 事件回传 ``conversation_id``，前端据此更新当前会话
    状态。未选中会话时为单轮问答（不持久化）。
    """

    st.header("❓ 文档问答")

    # 文档选择（可选限定范围）——仅在未选中会话时显示（会话已锁定文档范围）
    docs: list[DocumentInfo] = st.session_state.get("documents", [])
    ready_docs = [d for d in docs if d.status == "ready"]

    if not ready_docs:
        st.info("暂无可用文档（状态为「就绪」），请先上传并等待处理完成。")
        return

    current_conv_id: str | None = st.session_state.get("current_conversation_id")

    # 会话管理区（新建/切换/删除）
    _render_conversation_management(client)

    # 当前会话锁定文档范围时不显示文档选择（以会话锁定范围为准）
    if current_conv_id is None:
        selected = st.multiselect(
            "限定文档范围（不选则查询全部就绪文档）",
            options=ready_docs,
            format_func=lambda d: d.original_name,
        )
        selected_ids = [d.id for d in selected] if selected else None
    else:
        # 会话锁定文档范围，UI 提示当前范围
        conv_messages: list[MessageInfo] = st.session_state.get("conversation_messages", [])
        current_conv = st.session_state.get("current_conversation")
        if current_conv is not None and current_conv.document_ids:
            locked_names = [
                d.original_name for d in ready_docs if d.id in current_conv.document_ids
            ]
            if locked_names:
                st.caption(f"🔒 当前会话文档范围：{', '.join(locked_names)}")
        selected_ids = None  # 会话锁定时由 API 端决定范围
        # 历史消息回看
        _render_history(conv_messages)

    question = st.text_area(
        "输入你的问题",
        placeholder="例如：这篇论文的核心方法是什么？"
        if current_conv_id is None
        else "追问：刚才提到的方法再详细说说？",
        height=80,
    )

    if st.button("提问", type="primary", disabled=not question.strip()):
        # 流式问答：用闭包 holder 捕获 done/error 事件元数据，
        # token 事件文本 yield 给 st.write_stream 逐字渲染。
        holder: dict[str, object] = {
            "citations": [],
            "request_id": "",
            "elapsed_ms": 0,
            "conversation_id": current_conv_id,
            "error": None,
        }

        def _token_generator() -> Iterator[str]:
            try:
                for event in client.ask_question_stream(
                    question.strip(),
                    document_ids=selected_ids,
                    conversation_id=current_conv_id,
                ):
                    if isinstance(event, StreamToken):
                        yield event.text
                    elif isinstance(event, StreamDone):
                        holder["citations"] = event.citations
                        holder["request_id"] = event.request_id
                        holder["elapsed_ms"] = event.elapsed_ms
                        holder["conversation_id"] = event.conversation_id
                    elif isinstance(event, StreamError):
                        holder["error"] = event.detail
            except ApiClientError as exc:
                holder["error"] = exc.detail

        st.subheader("💡 答案")
        with st.spinner("正在检索和生成答案…"):
            # ``_token_generator`` 只 yield str，``st.write_stream`` 返回 str。
            answer = cast("str", st.write_stream(_token_generator()))

        error = holder["error"]
        if error is not None:
            st.error(f"问答失败：{error}")
            st.session_state.pop("last_query_result", None)
        else:
            result = QueryResult(
                answer=answer,
                citations=holder["citations"],  # type: ignore[arg-type]
                request_id=holder["request_id"],  # type: ignore[arg-type]
                elapsed_ms=holder["elapsed_ms"],  # type: ignore[arg-type]
                conversation_id=holder["conversation_id"],  # type: ignore[arg-type]
            )
            st.session_state["last_query_result"] = result
            st.caption(f"耗时 {result.elapsed_ms} ms | 请求 ID: `{result.request_id}`")
            _render_citations(result)
            # 更新当前会话状态（done 事件可能回传新的 conversation_id）
            new_conv_id = holder["conversation_id"]
            if isinstance(new_conv_id, str):
                st.session_state["current_conversation_id"] = new_conv_id
                # 追加本轮 user + assistant 消息到本地缓存
                msgs: list[MessageInfo] = st.session_state.get("conversation_messages", [])
                msgs.append(
                    MessageInfo(
                        id="",
                        role="user",
                        content=question.strip(),
                        citations=None,
                        created_at="",
                    )
                )
                msgs.append(
                    MessageInfo(
                        id="",
                        role="assistant",
                        content=answer,
                        citations=result.citations,
                        created_at="",
                    )
                )
                st.session_state["conversation_messages"] = msgs
                st.rerun()
    else:
        _render_query_result()


def _render_conversation_management(client: ApiClient) -> None:
    """渲染会话管理区：新建 / 列表切换 / 删除（阶段 9.2）。

    用 ``st.session_state`` 维护：
    - ``conversations``：会话列表缓存（新建/删除后刷新）
    - ``current_conversation_id``：当前选中会话 ID（``None`` 表示单轮模式）
    - ``current_conversation``：当前会话 ORM 对象（含 ``document_ids``）
    - ``conversation_messages``：当前会话的历史消息列表
    """

    st.subheader("💬 会话管理")

    col_new, col_refresh = st.columns([1, 1])
    with col_new:
        if st.button("➕ 新建会话", help="开始一个新的多轮对话"):
            # 新建会话：清空当前会话状态，进入单轮模式
            # 实际会话记录在首次提问时由 API 端按需创建（或用户先创建再提问）
            # 这里采用「先创建空会话」模式，便于用户提前锁定文档范围
            try:
                # 默认不锁定文档范围（None 表示全库）
                conv = client.create_conversation()
                st.session_state["current_conversation_id"] = conv.id
                st.session_state["current_conversation"] = conv
                st.session_state["conversation_messages"] = []
                _refresh_conversations()
                st.success(f"已新建会话：{conv.title or conv.id}")
                st.rerun()
            except ApiClientError as exc:
                st.error(f"新建会话失败：{exc.detail}")
    with col_refresh:
        if st.button("🔄 刷新会话列表"):
            _refresh_conversations()
            st.rerun()

    # 会话列表
    if "conversations" not in st.session_state:
        try:
            st.session_state["conversations"] = client.list_conversations()
        except ApiClientError as exc:
            st.error(f"无法获取会话列表：{exc.detail}")
            return

    convs: list[ConversationInfo] = st.session_state["conversations"]
    if not convs:
        st.caption("暂无会话。点击「新建会话」开始多轮对话，或直接提问进入单轮模式。")
        return

    current_conv_id = st.session_state.get("current_conversation_id")
    for conv in convs:
        with st.container(border=True):
            col1, col2, col3 = st.columns([5, 1, 1])
            with col1:
                title = conv.title or f"会话 {conv.id[:8]}"
                is_current = conv.id == current_conv_id
                prefix = "▶ " if is_current else ""
                st.write(f"**{prefix}{title}**")
                st.caption(f"ID: `{conv.id[:8]}…` | 更新于 {conv.updated_at}")
            with col2:
                if st.button("切换", key=f"sw-{conv.id}", disabled=is_current):
                    try:
                        full_conv = client.get_conversation(conv.id)
                        st.session_state["current_conversation_id"] = full_conv.id
                        st.session_state["current_conversation"] = full_conv
                        st.session_state["conversation_messages"] = full_conv.messages or []
                        st.session_state.pop("last_query_result", None)
                        st.rerun()
                    except ApiClientError as exc:
                        st.error(f"切换会话失败：{exc.detail}")
            with col3:
                if st.button("删除", key=f"del-conv-{conv.id}"):
                    try:
                        client.delete_conversation(conv.id)
                        if conv.id == current_conv_id:
                            st.session_state.pop("current_conversation_id", None)
                            st.session_state.pop("current_conversation", None)
                            st.session_state.pop("conversation_messages", None)
                            st.session_state.pop("last_query_result", None)
                        _refresh_conversations()
                        st.success("会话已删除")
                        st.rerun()
                    except ApiClientError as exc:
                        st.error(f"删除会话失败：{exc.detail}")


def _refresh_conversations() -> None:
    """清除 session_state 中的会话列表缓存，触发下次重新拉取。"""

    st.session_state.pop("conversations", None)


def _render_history(messages: list[MessageInfo]) -> None:
    """渲染当前会话的历史消息（user/assistant 交替，阶段 9.2）。

    历史消息只读展示，不提供编辑/删除单条消息的功能（避免与 DB 持久化
    状态不一致）。``assistant`` 消息含 ``[C1]`` 等引用标记原文和 ``citations``
    快照，渲染时同时展示答案文本和折叠的引用详情。
    """

    if not messages:
        return

    st.subheader(f"📜 历史对话（{len(messages)} 条消息）")
    for msg in messages:
        if msg.role == "user":
            with st.chat_message("user"):
                st.write(msg.content)
        elif msg.role == "assistant":
            with st.chat_message("assistant"):
                st.write(msg.content)
                if msg.citations:
                    with st.expander(f"📎 引用详情（{len(msg.citations)} 条）", expanded=False):
                        for idx, cite in enumerate(msg.citations, start=1):
                            page_range = _format_page_range(cite.start_page, cite.end_page)
                            st.write(
                                f"**[C{idx}] {cite.document_name} · {page_range} · "
                                f"片段 {cite.chunk_index} · 分数 {cite.score:.4f}**"
                            )
                            st.text(cite.snippet)
                            st.divider()


def _format_page_range(start: int, end: int) -> str:
    """格式化页码范围展示文案。

    - ``start == end``：返回 ``"第X页"``（单页）
    - ``start != end``：返回 ``"第X-Y页"``（跨页范围）
    """

    if start == end:
        return f"第 {start} 页"
    return f"第 {start}-{end} 页"


def _render_query_result() -> None:
    """渲染上一次问答的答案和引用（rerun 时从 session_state 恢复）。"""

    result: QueryResult | None = st.session_state.get("last_query_result")
    if result is None:
        return

    st.subheader("💡 答案")
    st.markdown(result.answer)
    st.caption(f"耗时 {result.elapsed_ms} ms | 请求 ID: `{result.request_id}`")
    _render_citations(result)


def _render_citations(result: QueryResult) -> None:
    """渲染引用详情区（答案已由流式或 markdown 渲染，这里只渲染引用）。"""

    if not result.citations:
        st.info("本次回答未附带引用。")
        return

    st.subheader(f"📎 引用详情（{len(result.citations)} 条）")
    for idx, cite in enumerate(result.citations, start=1):
        page_range = _format_page_range(cite.start_page, cite.end_page)
        label = f"[C{idx}] {cite.document_name} · {page_range} · 片段 {cite.chunk_index}"
        with st.expander(label, expanded=False):
            st.write(f"**来源文档**：{cite.document_name}（`{cite.document_id}`）")
            st.write(f"**页码**：{page_range}")
            st.write(f"**片段序号**：{cite.chunk_index}")
            st.write(f"**相似度分数**：{cite.score:.4f}")
            st.divider()
            st.write("**原文片段**：")
            st.text(cite.snippet)


def main() -> None:
    """Streamlit 应用主入口（由 ``streamlit run`` 调用）。"""

    _render_header()
    client = _get_client()
    _render_document_management(client)
    _render_document_list(client)
    st.divider()
    _render_qa(client)


if __name__ == "__main__":
    main()
