"""Streamlit 演示界面入口（ChatGPT 风格布局）。

依据 ``docs/ui_feedback_2026_07_25.md`` 问题 2/3/4 改造：
- **左右分栏**（左 25% 会话+文档管理，右 75% 聊天区），替代原单栏垂直堆叠
- **用 ``st.chat_message`` + ``st.chat_input``** 实现标准 AI 问答界面，
  解决「提问后无衔接」「多轮视觉不清晰」问题（问题 2/3）
- **新建会话时让用户选文档范围**（``client.create_conversation(document_ids=...)``），
  修复多文档会话只检索到一篇的 Bug（问题 4 根因）
- **引用卡片标注来源文档名**（``[C1] paper1.pdf · 第3页``），多文档场景可直观区分

启动方式（两个服务分开运行）：
1. 先启动 FastAPI API 服务（端口 8000）：
   ``uv run uvicorn research_rag.api.app:create_app --factory --port 8000``
2. 再启动 Streamlit 界面（端口 8501）：
   ``uv run streamlit run src/research_rag/ui/app.py --server.port 8501``

设计取舍（初学者向说明）：
- **UI 层只调 HTTP API**：本界面通过 ``ApiClient`` 调用 FastAPI，不直接
  import ``DocumentService`` / ``QaService``。所有业务逻辑（PDF 解析、
  向量检索、LLM 问答）都在 API 服务端完成，UI 只负责展示和交互
  （PROJECT_PLAN 第 13.6 节分层）。
- **左右分栏用 ``st.columns([1, 3])``**：不用 ``st.sidebar``（sidebar 在宽屏
  占比不稳定且不可内部滚动）。``st.columns`` 在 ``layout="wide"`` 下能稳定
  实现 25%/75% 分栏，左侧栏可内部滚动。
- **``st.chat_input`` 固定底部**：回车自动发送、自动清空，解决「提问后无衔接」
  问题（问题 3）。原生支持多轮视觉（每轮 user/assistant 气泡明确分隔）。
- **流式输出用 ``st.write_stream`` 渲染到 ``st.chat_message("assistant")`` 内**：
  token 逐字渲染到 assistant 气泡，流结束后引用卡片渲染在同气泡下方
  （问题 2 布局要求）。
- **会话文档范围锁定（问题 4 修复）**：左侧栏文档多选 + 「新建会话」按钮，
  新建时调 ``client.create_conversation(document_ids=selected_ids)`` 锁定范围。
  会话锁定后右侧不再显示文档选择，改显示当前范围标签；后端
  ``QaService.answer_stream`` 用会话锁定的 ``document_ids`` 检索，避免
  「全库检索时 top_k 被一篇占满」的问题。
- **单轮模式也支持文档范围限定**：未选中会话时，左侧选中的文档范围会传给
  ``ask_question_stream(document_ids=selected_ids)``，让单轮问答也能限定范围。
- **``st.session_state`` 缓存文档/会话列表**：上传/删除/新建后刷新缓存，
  避免 rerun 时重新请求。
- **不加载 .env**：Streamlit 只需 ``API_BASE_URL``（有默认值），LLM 密钥等
  由 API 服务端加载。UI 不接触敏感配置。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import streamlit as st
import streamlit.components.v1 as components

from research_rag.ui.api_client import (
    ApiClient,
    ApiClientError,
    Citation,
    ConversationInfo,
    DocumentInfo,
    MessageInfo,
    StreamDone,
    StreamError,
    StreamToken,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, MutableMapping

# 缓存 ApiClient 实例，避免每次 rerun 重建
_CLIENT_KEY = "_api_client"
# session_state key：左侧栏选中的文档 ID 列表，供单轮问答和新建会话使用
_PENDING_DOC_IDS_KEY = "_pending_doc_ids"

# 导航分组默认展开状态（Issue #109 左侧导航重构）
# history / docs 默认展开，其他分组默认折叠
_NAV_SECTION_DEFAULT_EXPANDED: dict[str, bool] = {
    "history": True,
    "docs": True,
}


def _is_nav_section_expanded(section_key: str, session_state: MutableMapping[str, object]) -> bool:
    """判断导航分组是否展开（Issue #109）。

    优先读 ``session_state[f"nav-{section_key}-expanded"]``，未设置时用
    ``_NAV_SECTION_DEFAULT_EXPANDED`` 的默认值（``history`` / ``docs`` 默认
    展开，其他默认折叠）。

    Args:
        section_key: 导航分组 key（如 ``"history"`` / ``"docs"``）。
        session_state: ``st.session_state`` 或测试用普通 dict。

    Returns:
        是否展开。
    """

    state_key = f"nav-{section_key}-expanded"
    if state_key in session_state:
        return bool(session_state[state_key])
    return _NAV_SECTION_DEFAULT_EXPANDED.get(section_key, False)


def _is_sidebar_collapsed(session_state: MutableMapping[str, object]) -> bool:
    """判断左侧栏是否整体收起（Issue #109）。

    优先读 ``session_state["sidebar-collapsed"]``，未设置时默认不折叠
    （返回 ``False``）。

    Args:
        session_state: ``st.session_state`` 或测试用普通 dict。

    Returns:
        是否收起。
    """

    return bool(session_state.get("sidebar-collapsed", False))


# 输入栏下方免责声明（Issue #112）
_UPLOAD_DISCLAIMER = "AI 可能出错，请核实重要信息"


def _is_valid_pdf_filename(filename: str) -> bool:
    """校验上传文件名是否为 PDF 扩展名（大小写不敏感，Issue #112）。

    Args:
        filename: 上传文件名。

    Returns:
        是否为 PDF 文件。
    """

    return filename.lower().endswith(".pdf")


def _get_chat_layout_css(max_width_px: int = 800) -> str:
    """生成对话区居中布局的 CSS（Issue #111）。

    限制对话区消息流与输入栏最大宽度并居中，视觉更聚焦（贴近截图风格）。
    user/assistant 气泡对齐依赖 Streamlit 原生行为（user 右对齐、assistant
    左对齐），本 CSS 只负责宽度收窄与居中。

    Args:
        max_width_px: 对话区最大宽度（像素），默认 800。

    Returns:
        CSS 字符串（含 ``<style>`` 标签）。
    """

    return f"""
<style>
/* 对话区消息流居中 + 宽度收窄（Issue #111） */
div[data-testid="stChatMessage"] {{
    max-width: {max_width_px}px;
    margin-left: auto;
    margin-right: auto;
}}
/* 输入栏居中 */
div[data-testid="stChatInput"] {{
    max-width: {max_width_px}px;
    margin-left: auto;
    margin-right: auto;
}}
</style>
"""


def _strip_markdown_to_plain_text(text: str) -> str:
    """去除 markdown 标记，提取纯文本（Issue #113）。

    覆盖常见标记：行内代码、链接、粗体、斜体、标题。复杂 markdown（表格、
    列表）不做完整解析，仅清理常见标记。

    Args:
        text: 含 markdown 标记的文本。

    Returns:
        纯文本。
    """

    # 行内代码 `code` → code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 链接 [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 粗体 **text** → text
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    # 斜体 *text* → text
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # 标题 # text → text（行首，多个 #）
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text


def _render_copy_button(text: str, key_suffix: str) -> None:
    """渲染复制按钮（Issue #113）。

    点击后用 JS 注入 ``navigator.clipboard.writeText`` 把纯文本（去除
    markdown 标记）写入剪贴板，并显示 ``st.toast`` 提示。

    Args:
        text: 要复制的原始文本（含 markdown 标记）。
        key_suffix: 按钮 key 后缀（保证唯一性，如 request_id 或消息索引）。
    """

    if st.button("📋 复制", key=f"copy-{key_suffix}"):
        plain_text = _strip_markdown_to_plain_text(text)
        # 转义 JS 字符串字面量
        escaped = (
            plain_text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        components.html(
            f"<script>navigator.clipboard.writeText('{escaped}');</script>",
            height=0,
        )
        st.toast("已复制到剪贴板")


def _render_nav_section(
    icon: str,
    label: str,
    section_key: str,
    render_content: Callable[[], None],
) -> None:
    """渲染可折叠的导航分组（Issue #109）。

    标题行带图标 + 标签 + 折叠箭头（▶/▼），点击切换展开状态（持久化到
    ``session_state[f"nav-{section_key}-expanded"]``）。展开时调用
    ``render_content`` 渲染分组内容。

    用 ``st.button`` 标题行 + 手动控制展开（而非 ``st.expander``），因为
    ``st.expander`` 的 ``expanded`` 参数每次 rerun 都重置，无法持久化用户
    手动折叠的状态。

    Args:
        icon: 分组图标（emoji）。
        label: 分组标签。
        section_key: 分组 key（用于 session_state 持久化展开状态）。
        render_content: 展开时调用的渲染函数。
    """

    session_state = cast("MutableMapping[str, object]", st.session_state)
    expanded = _is_nav_section_expanded(section_key, session_state)
    arrow = "▼" if expanded else "▶"
    if st.button(
        f"{icon} {label} {arrow}",
        key=f"nav-section-{section_key}",
        use_container_width=True,
    ):
        st.session_state[f"nav-{section_key}-expanded"] = not expanded
        st.rerun()
    if expanded:
        render_content()


def _get_client() -> ApiClient:
    """获取缓存的 ``ApiClient`` 实例。"""

    if _CLIENT_KEY not in st.session_state:
        st.session_state[_CLIENT_KEY] = ApiClient()
    client: ApiClient = st.session_state[_CLIENT_KEY]
    return client


def _refresh_documents() -> None:
    """清除 session_state 中的文档列表缓存，触发下次重新拉取。"""

    st.session_state.pop("documents", None)


def _refresh_conversations() -> None:
    """清除 session_state 中的会话列表缓存，触发下次重新拉取。"""

    st.session_state.pop("conversations", None)


def _format_page_range(start: int, end: int) -> str:
    """格式化页码范围展示文案。

    - ``start == end``：返回 ``"第X页"``（单页）
    - ``start != end``：返回 ``"第X-Y页"``（跨页范围）
    """

    if start == end:
        return f"第 {start} 页"
    return f"第 {start}-{end} 页"


def _get_ready_documents() -> list[DocumentInfo]:
    """从 session_state 获取就绪文档列表，未缓存时返回空列表。"""

    docs: list[DocumentInfo] = st.session_state.get("documents", [])
    return [d for d in docs if d.status == "ready"]


def _ensure_documents_loaded(client: ApiClient) -> None:
    """确保文档列表已加载到 session_state（首次访问时拉取）。"""

    if "documents" not in st.session_state:
        try:
            st.session_state["documents"] = client.list_documents()
        except ApiClientError as exc:
            st.session_state["documents"] = []
            st.error(f"无法获取文档列表：{exc.detail}")


def _ensure_conversations_loaded(client: ApiClient) -> None:
    """确保会话列表已加载到 session_state。"""

    if "conversations" not in st.session_state:
        try:
            st.session_state["conversations"] = client.list_conversations()
        except ApiClientError as exc:
            st.session_state["conversations"] = []
            st.error(f"无法获取会话列表：{exc.detail}")


# ---------------------------------------------------------------------------
# 左侧栏：新建会话 + 会话列表 + 文档管理
# ---------------------------------------------------------------------------


def _render_sidebar(client: ApiClient) -> None:
    """渲染左侧栏：上组（新聊天/搜索/会话列表/文档列表）+ 下组（设置/帮助）。

    Issue #109 左侧导航重构：图标分组 + 可折叠会话/文档列表，贴近 ChatGPT 视觉。

    文档范围选择同时用于：
    - 新建会话时锁定 ``document_ids``（解决问题 4 根因）
    - 单轮问答时限定检索范围（未选中会话时）

    「新聊天」点击后清空对话区，进入空白状态（不自动创建会话，发首条
    消息时才 ``create_conversation``，沿用 ``_handle_question`` 现有逻辑）。
    """

    _ensure_documents_loaded(client)
    ready_docs = _get_ready_documents()

    # 文档范围选择（用于新建会话锁定范围 + 单轮问答限定范围）
    selected_docs = st.multiselect(
        "选中文档范围",
        options=ready_docs,
        format_func=lambda d: d.original_name,
        help="选中后新建会话将锁定这些文档；未选中会话时单轮问答也用此范围。",
    )
    selected_ids: list[str] | None = [d.id for d in selected_docs] if selected_docs else None
    # 缓存到 session_state，供右侧单轮问答读取
    st.session_state[_PENDING_DOC_IDS_KEY] = selected_ids

    # --- 上组：新聊天 / 搜索会话 / 历史会话列表 / 文档列表 ---
    if st.button("✏️ 新聊天", use_container_width=True, key="nav-new-chat"):
        # 清空对话区，进入空白状态（不自动创建会话，发首条消息时才创建）
        st.session_state.pop("current_conversation_id", None)
        st.session_state.pop("current_conversation", None)
        st.session_state.pop("conversation_messages", None)
        st.rerun()

    # 搜索会话
    search_query = st.text_input(
        "搜索会话",
        value="",
        placeholder="🔍 按标题搜索会话",
        key="nav-search-conversations",
        label_visibility="collapsed",
    )

    # 历史会话列表（可折叠，默认展开）
    _render_nav_section(
        "💬",
        "历史会话列表",
        "history",
        lambda: _render_conversation_list(client, search_query=search_query),
    )

    st.divider()

    # 文档列表（可折叠，默认展开；含上传 + 列表 + 选择 + 删除）
    # 上传按钮暂保留于此，待 #112 迁移到输入栏「+」
    _render_nav_section(
        "📄",
        "文档列表",
        "docs",
        lambda: _render_document_management(client),
    )

    st.divider()

    # --- 下组：设置 / 帮助 ---
    if st.button("⚙️ 设置", use_container_width=True, key="nav-settings"):
        st.info("设置功能开发中")  # 占位

    if st.button("❓ 帮助", use_container_width=True, key="nav-help"):
        st.info("帮助功能开发中")  # 占位


def _render_conversation_list(client: ApiClient, search_query: str = "") -> None:
    """渲染会话列表（点击切换 / 删除，阶段 9.2 + Issue #109 搜索过滤）。

    每条会话显示标题（当前会话加 ▶ 前缀），点击切换时从 API 拉取完整消息列表。
    ``search_query`` 非空时按标题过滤（大小写不敏感）。

    Args:
        client: API 客户端。
        search_query: 搜索关键词（按标题过滤，空字符串不过滤）。
    """

    _ensure_conversations_loaded(client)
    convs: list[ConversationInfo] = st.session_state.get("conversations", [])
    # 搜索过滤（Issue #109）
    query = search_query.strip().lower()
    if query:
        convs = [c for c in convs if query in (c.title or "").lower()]
    if not convs:
        if query:
            st.caption("未找到匹配的会话。")
        else:
            st.caption("暂无会话。点击上方「新聊天」开始多轮对话。")
        return

    current_conv_id = st.session_state.get("current_conversation_id")
    for conv in convs:
        is_current = conv.id == current_conv_id
        title = conv.title or f"会话 {conv.id[:8]}"
        prefix = "▶ " if is_current else ""
        col1, col2 = st.columns([4, 1])
        with col1:
            if st.button(
                f"{prefix}{title}",
                key=f"sw-{conv.id}",
                use_container_width=True,
                help=f"切换到会话（ID: {conv.id[:8]}…）",
            ):
                try:
                    full_conv = client.get_conversation(conv.id)
                    st.session_state["current_conversation_id"] = full_conv.id
                    st.session_state["current_conversation"] = full_conv
                    st.session_state["conversation_messages"] = full_conv.messages or []
                    _refresh_conversations()
                    st.rerun()
                except ApiClientError as exc:
                    st.error(f"切换会话失败：{exc.detail}")
        with col2:
            if st.button("🗑", key=f"del-conv-{conv.id}", help="删除会话"):
                try:
                    client.delete_conversation(conv.id)
                    if conv.id == current_conv_id:
                        st.session_state.pop("current_conversation_id", None)
                        st.session_state.pop("current_conversation", None)
                        st.session_state.pop("conversation_messages", None)
                    _refresh_conversations()
                    st.rerun()
                except ApiClientError as exc:
                    st.error(f"删除会话失败：{exc.detail}")


def _render_document_management(client: ApiClient) -> None:
    """渲染文档列表区：列表 + 删除（上传入口已迁移到输入栏「+」，Issue #112）。"""

    docs: list[DocumentInfo] = st.session_state.get("documents", [])
    if not docs:
        st.caption("暂无文档。点击下方输入栏「➕」上传 PDF。")
        return

    for doc in docs:
        with st.container(border=True):
            status_label = {
                "ready": "✅",
                "pending": "⏳",
                "failed": "❌",
            }.get(doc.status, doc.status)
            col1, col2 = st.columns([5, 1])
            with col1:
                st.write(f"{status_label} **{doc.original_name}**")
                st.caption(f"{doc.page_count} 页 | 创建于 {doc.created_at}")
                if doc.error_message:
                    st.caption(f"⚠️ {doc.error_message}")
            with col2:
                if st.button("删除", key=f"del-{doc.id}", help="删除文档及其向量"):
                    try:
                        client.delete_document(doc.id)
                        _refresh_documents()
                        st.rerun()
                    except ApiClientError as exc:
                        st.error(f"删除失败：{exc.detail}")


# ---------------------------------------------------------------------------
# 右侧主区：消息流 + 底部输入框
# ---------------------------------------------------------------------------


def _render_chat(client: ApiClient) -> None:
    """渲染右侧聊天区：状态栏 + 历史消息流 + 底部输入框。

    - 历史消息从 ``session_state["conversation_messages"]`` 加载，用
      ``st.chat_message`` 渲染 user/assistant 交替气泡
    - 底部 ``st.chat_input`` 回车发送，自动清空，解决多轮衔接问题（问题 3）
    - 流式输出用 ``st.write_stream`` 渲染到 ``st.chat_message("assistant")`` 内
    - Issue #111：注入 CSS 限制对话区宽度并居中，视觉更聚焦
    """

    # 注入对话区居中布局 CSS（Issue #111）
    st.markdown(_get_chat_layout_css(), unsafe_allow_html=True)

    _ensure_documents_loaded(client)
    ready_docs = _get_ready_documents()
    if not ready_docs:
        st.info("暂无可用文档（状态为「就绪」），请点击下方输入栏「➕」上传 PDF。")
        return

    current_conv_id: str | None = st.session_state.get("current_conversation_id")
    current_conv: ConversationInfo | None = st.session_state.get("current_conversation")

    # 顶部状态栏：显示当前会话信息或单轮模式提示
    if current_conv_id is not None and current_conv is not None:
        title = current_conv.title or f"会话 {current_conv_id[:8]}"
        st.caption(f"💬 当前会话：**{title}**")
        if current_conv.document_ids:
            locked_names = [
                d.original_name for d in ready_docs if d.id in current_conv.document_ids
            ]
            if locked_names:
                st.caption(f"🔒 文档范围：{', '.join(locked_names)}")
            else:
                st.caption("🔒 文档范围：已锁定（文档可能已删除）")
        else:
            st.caption("🔓 文档范围：全库")
    else:
        pending_ids: list[str] | None = st.session_state.get(_PENDING_DOC_IDS_KEY)
        if pending_ids:
            selected_names = [d.original_name for d in ready_docs if d.id in pending_ids]
            if selected_names:
                st.caption(f"💬 单轮模式 | 文档范围：{', '.join(selected_names)}")
            else:
                st.caption("💬 单轮模式（未选中会话，提问不持久化）")
        else:
            st.caption("💬 单轮模式 | 文档范围：全库（未选中会话，提问不持久化）")

    # 渲染历史消息
    msgs: list[MessageInfo] = st.session_state.get("conversation_messages", [])
    # 批量初始化历史消息反馈状态（Issue #92）：进入历史会话时查询每条
    # assistant 消息的反馈状态，供 _render_feedback_buttons 读取初始高亮。
    # 已有状态不覆盖（用户本会话刚操作的反馈保留）。
    # cast：streamlit SessionStateProxy 运行时实现 MutableMapping 接口，
    # 但静态类型未声明，用 cast 桥接（函数内只用 in/[]=/[] 操作）。
    _init_feedback_state_for_history(
        client,
        msgs,
        cast("MutableMapping[str, object]", st.session_state),
    )
    for idx, msg in enumerate(msgs):
        with st.chat_message(msg.role):
            st.write(msg.content)
            if msg.citations:
                _render_citations_inline(msg.citations)
            # 复制按钮（仅 assistant 消息，Issue #113）
            if msg.role == "assistant":
                key_suffix = msg.id or msg.request_id or f"idx-{idx}"
                _render_copy_button(msg.content, key_suffix=key_suffix)
            # 历史消息反馈按钮（Issue #92）：仅 assistant 消息且有 request_id 时渲染。
            # 旧消息（request_id=None）隐藏按钮——点击必然 404，体验差。
            if _should_render_feedback_for_message(msg):
                # _should_render_feedback_for_message 已保证 request_id 非 None，
                # 用 cast 协助 mypy 类型收窄（运行时 cast 无操作）。
                _render_feedback_buttons(client, cast("str", msg.request_id))

    # 输入栏工具栏：「+」上传按钮 + 免责声明（Issue #112）
    _render_input_toolbar(client)

    # 底部输入框（回车发送，自动清空）
    question = st.chat_input("输入你的问题，回车发送…")
    if question and question.strip():
        # 单轮模式用左侧选中的范围；会话模式由 API 端用会话锁定范围（传 None）
        doc_ids_for_query = (
            None if current_conv_id is not None else st.session_state.get(_PENDING_DOC_IDS_KEY)
        )
        _handle_question(client, question.strip(), current_conv_id, doc_ids_for_query)


def _render_input_toolbar(client: ApiClient) -> None:
    """渲染输入栏上方工具栏：「+」上传按钮 + 免责声明（Issue #112）。

    Streamlit ``st.chat_input`` 固定底部且无法嵌入其他组件，因此在
    ``st.chat_input`` 上方渲染一行工具栏：左侧「➕」按钮用 ``st.popover``
    包裹文件上传（选择文件后自动上传），右侧显示免责声明小字。
    """

    col_upload, col_disclaimer = st.columns([1, 4])
    with col_upload, st.popover("➕", use_container_width=True):
        st.caption("上传 PDF 文档")
        uploaded = st.file_uploader(
            "选择 PDF 文件",
            type=["pdf"],
            help="上传后将自动解析、切分并建立向量索引。",
            key="input-toolbar-uploader",
        )
        if uploaded is not None:
            if not _is_valid_pdf_filename(uploaded.name):
                st.error("仅支持 PDF 文件")
            else:
                try:
                    with st.spinner("正在上传并解析文档…"):
                        doc = client.upload_document(uploaded.getvalue(), uploaded.name)
                    st.success(
                        f"上传成功：{doc.original_name}（{doc.page_count} 页，状态：{doc.status}）"
                    )
                    _refresh_documents()
                    st.rerun()
                except ApiClientError as exc:
                    st.error(f"上传失败：{exc.detail}")
    with col_disclaimer:
        st.caption(_UPLOAD_DISCLAIMER)


def _handle_question(
    client: ApiClient,
    question: str,
    current_conv_id: str | None,
    document_ids: list[str] | None,
) -> None:
    """处理用户提问：渲染 user 气泡 + 流式渲染 assistant 答案 + 引用 + 更新会话状态。

    流程：
    1. ``st.chat_message("user")`` 渲染用户问题
    2. ``st.chat_message("assistant")`` 内用 ``st.write_stream`` 逐字渲染 LLM 答案
    3. 流结束后在 assistant 气泡内渲染引用卡片
    4. 若 ``done`` 事件回传 ``conversation_id``（首次提问时 API 端按需创建会话），
       更新 ``current_conversation_id`` 并把 user+assistant 消息追加到本地缓存
    5. ``st.rerun()`` 触发重渲染，历史消息从 ``conversation_messages`` 加载

    Args:
        client: API 客户端。
        question: 用户问题（已 strip）。
        current_conv_id: 当前会话 ID（``None`` 表示单轮模式）。
        document_ids: 限定查询的文档 ID 列表。会话模式下为 ``None``（由 API 端
            用会话锁定的范围）；单轮模式下用左侧选中的范围。
    """

    # 渲染 user 消息气泡
    with st.chat_message("user"):
        st.write(question)

    # 流式渲染 assistant 答案
    with st.chat_message("assistant"):
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
                    question,
                    document_ids=document_ids,
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

        with st.spinner("正在检索和生成答案…"):
            # ``_token_generator`` 只 yield str，``st.write_stream`` 返回 str。
            answer = cast("str", st.write_stream(_token_generator()))

        error = holder["error"]
        if error is not None:
            st.error(f"问答失败：{error}")
            return

        # 渲染引用卡片（在 assistant 气泡内）
        citations: list[Citation] = holder["citations"]  # type: ignore[assignment]
        if citations:
            _render_citations_inline(citations)

        elapsed_ms = holder["elapsed_ms"]
        request_id = str(holder["request_id"])
        st.caption(f"耗时 {elapsed_ms} ms | 请求 ID: `{request_id}`")

        # 复制按钮（Issue #113）
        _render_copy_button(answer, key_suffix=request_id)

        # 渲染点赞/点踩按钮（阶段 10.2 前端补充，对接 feedback API）
        if request_id:
            _render_feedback_buttons(client, request_id)

        # 更新会话状态（done 事件可能回传新的 conversation_id）
        new_conv_id = holder["conversation_id"]
        if isinstance(new_conv_id, str):
            st.session_state["current_conversation_id"] = new_conv_id
            # 追加本轮 user + assistant 消息到本地缓存
            msgs: list[MessageInfo] = st.session_state.get("conversation_messages", [])
            msgs.append(
                MessageInfo(
                    id="",
                    role="user",
                    content=question,
                    citations=None,
                    created_at="",
                )
            )
            msgs.append(
                MessageInfo(
                    id="",
                    role="assistant",
                    content=answer,
                    citations=citations,
                    request_id=request_id,
                    created_at="",
                )
            )
            st.session_state["conversation_messages"] = msgs
            _refresh_conversations()
            st.rerun()


def _feedback_state_key(request_id: str) -> str:
    """构造反馈状态在 ``session_state`` 中的 key（``feedback-{request_id}``）。

    ``_init_feedback_state_for_history`` 与 ``_render_feedback_buttons`` 共享
    此 key 格式，提取为 helper 避免格式漂移。
    """

    return f"feedback-{request_id}"


def _should_render_feedback_for_message(msg: MessageInfo) -> bool:
    """判断历史消息是否应渲染反馈按钮（Issue #92）。

    仅 assistant 消息且有 ``request_id`` 时渲染；user 消息与旧消息
    （``request_id=None``）隐藏按钮——旧消息点击反馈必然 404，体验差，
    隐藏比禁用+tooltip 更干净。

    Args:
        msg: 历史会话消息。

    Returns:
        是否渲染反馈按钮。
    """

    return msg.role == "assistant" and msg.request_id is not None


def _init_feedback_state_for_history(
    client: ApiClient,
    messages: list[MessageInfo],
    session_state: MutableMapping[str, object],
) -> None:
    """批量初始化历史会话消息的反馈状态到 ``session_state``。

    进入历史会话时调用：对每条有 ``request_id`` 的 assistant 消息，查询
    ``get_feedback`` 反馈状态，写入 ``session_state[f"feedback-{request_id}"]``
    （``None`` / ``"like"`` / ``"dislike"``），供 ``_render_feedback_buttons``
    读取初始按钮高亮状态。

    旧消息（``request_id=None``）与 user 消息跳过（不写 key，不调
    ``get_feedback``）。``session_state`` 已有同 key 时不覆盖，避免 rerun
    时丢失用户刚操作的反馈。

    Args:
        client: API 客户端，用于调 ``get_feedback``。
        messages: 历史会话消息列表（user + assistant 交替）。
        session_state: Streamlit ``st.session_state``（``SessionStateProxy``）
            或测试用的普通 dict。用 ``MutableMapping`` 接口收两者。
    """

    for msg in messages:
        if msg.request_id is None:
            continue
        state_key = _feedback_state_key(msg.request_id)
        if state_key in session_state:
            # 已有状态（用户本会话刚操作过），不覆盖
            continue
        feedback = client.get_feedback(msg.request_id)
        session_state[state_key] = feedback.rating if feedback is not None else None


def _render_feedback_buttons(client: ApiClient, request_id: str) -> None:
    """在 assistant 消息气泡内渲染点赞/点踩按钮，对接 feedback API。

    状态管理（``st.session_state``）：
    - ``feedback-{request_id}``：当前反馈状态（``None`` / ``"like"`` / ``"dislike"``）
    - ``comment-input-{request_id}``：点踩时的文字评论（可选，最长 2000 字符）

    交互逻辑：
    - 点击未选按钮：``POST /feedback`` 提交（Upsert 语义自动覆盖之前的选择）
    - 再次点击已选按钮：``DELETE /feedback/{request_id}`` 撤销
    - 点踩后展开 ``st.text_area`` 收集评论，提交时再次 POST 带 comment（Upsert 更新）

    Args:
        client: API 客户端，用于调 feedback 端点。
        request_id: 关联的问答 request_id（来自 SSE ``done`` 事件）。
    """

    state_key = _feedback_state_key(request_id)
    current_rating: str | None = st.session_state.get(state_key)

    # 按钮行：点赞 / 点踩（已选时用 primary 高亮）
    col_like, col_dislike = st.columns(2)

    with col_like:
        like_clicked = st.button(
            "👍 点赞",
            key=f"like-{request_id}",
            type="primary" if current_rating == "like" else "secondary",
            use_container_width=True,
        )

    with col_dislike:
        dislike_clicked = st.button(
            "👎 点踩",
            key=f"dislike-{request_id}",
            type="primary" if current_rating == "dislike" else "secondary",
            use_container_width=True,
        )

    # 处理点赞点击
    if like_clicked:
        try:
            if current_rating == "like":
                client.delete_feedback(request_id)
                st.session_state[state_key] = None
            else:
                # Upsert：POST 同 request_id 自动覆盖之前的点踩
                client.submit_feedback(request_id, rating="like")
                st.session_state[state_key] = "like"
            st.rerun()
        except ApiClientError as exc:
            st.error(f"反馈提交失败：{exc.detail}")

    # 处理点踩点击
    if dislike_clicked:
        try:
            if current_rating == "dislike":
                client.delete_feedback(request_id)
                st.session_state[state_key] = None
                # 清空评论缓存，避免下次点踩时残留
                st.session_state.pop(f"comment-input-{request_id}", None)
            else:
                client.submit_feedback(request_id, rating="dislike")
                st.session_state[state_key] = "dislike"
            st.rerun()
        except ApiClientError as exc:
            st.error(f"反馈提交失败：{exc.detail}")

    # 点踩后展开评论输入框（可选，Upsert 更新带 comment）
    if current_rating == "dislike":
        comment = st.text_area(
            "告诉我们哪里不好（可选）",
            key=f"comment-input-{request_id}",
            max_chars=2000,
            height=80,
        )
        if st.button("提交评论", key=f"submit-comment-{request_id}"):
            if comment and comment.strip():
                try:
                    client.submit_feedback(request_id, rating="dislike", comment=comment.strip())
                    st.success("评论已提交，感谢反馈！")
                except ApiClientError as exc:
                    st.error(f"评论提交失败：{exc.detail}")
            else:
                st.warning("评论不能为空")


def _render_citations_inline(citations: list[Citation]) -> None:
    """在 assistant 消息气泡内渲染引用详情（折叠式）。

    引用标题明确标注来源文档名，便于多文档场景区分（问题 4 改进）：
    ``[C1] paper1.pdf · 第3页 · 片段 5``

    Args:
        citations: 引用列表（按模型引用顺序）。
    """

    if not citations:
        return

    with st.expander(f"📎 引用详情（{len(citations)} 条）", expanded=False):
        for idx, cite in enumerate(citations, start=1):
            page_range = _format_page_range(cite.start_page, cite.end_page)
            st.write(f"**[C{idx}] {cite.document_name} · {page_range} · 片段 {cite.chunk_index}**")
            st.caption(f"相似度分数 {cite.score:.4f} | 文档 ID: `{cite.document_id[:8]}…`")
            st.text(cite.snippet)
            st.divider()


def main() -> None:
    """Streamlit 应用主入口（由 ``streamlit run`` 调用）。"""

    st.set_page_config(
        page_title="科研文献智能问答",
        page_icon="📚",
        layout="wide",
    )
    st.title("📚 科研文献可溯源智能问答系统")
    st.caption("上传 PDF → 提问 → 获取带引用的答案。所有处理由 FastAPI 后端完成。")

    client = _get_client()

    # Issue #109：左侧栏可整体折叠。折叠时只渲染展开按钮，右侧聊天区占满。
    session_state = cast("MutableMapping[str, object]", st.session_state)
    if _is_sidebar_collapsed(session_state):
        # 折叠状态：左侧仅展开按钮，右侧占满
        col_toggle, col_main = st.columns([1, 24], gap="small")
        with col_toggle:
            if st.button("☰", help="展开左侧栏"):
                st.session_state["sidebar-collapsed"] = False
                st.rerun()
        with col_main:
            _render_chat(client)
    else:
        # 展开状态：左侧栏 + 右侧聊天区
        left_col, right_col = st.columns([1, 3], gap="medium")
        with left_col:
            # 顶部折叠按钮
            if st.button("☰", help="收起左侧栏"):
                st.session_state["sidebar-collapsed"] = True
                st.rerun()
            _render_sidebar(client)
        with right_col:
            _render_chat(client)


if __name__ == "__main__":
    main()
