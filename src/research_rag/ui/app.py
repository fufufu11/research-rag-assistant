"""Streamlit 演示界面入口。

依据 PROJECT_PLAN.md 第 716-722 行（阶段 6 验收：浏览器完成完整流程）、
第 13.6 节（UI 层只调 API，不直接 import 业务层）。

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
- **界面分三块**：文档管理（上传/列表/删除）、问答（输入框 + 带引用展示）、
  引用详情（``st.expander`` 折叠原文片段）。对齐验收标准"上传 PDF → 查看
  文档列表 → 提问并查看带引用的答案 → 删除文档"。
- **错误用 ``st.error`` 展示**：``ApiClientError`` 捕获后直接展示 detail，
  不阻断整个界面（其他功能区仍可用）。
- **``st.session_state`` 缓存文档列表**：上传/删除后刷新缓存，避免每次
  重新请求。问答结果也存 session_state，便于引用展开时回看。
- **不加载 .env**：Streamlit 只需 ``API_BASE_URL``（有默认值），LLM 密钥等
  由 API 服务端（``run_server.py``）加载。UI 不接触敏感配置。
"""

from __future__ import annotations

import streamlit as st

from research_rag.ui.api_client import ApiClient, ApiClientError, DocumentInfo, QueryResult

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
    """渲染问答区：输入框 + 答案 + 引用展示。"""

    st.header("❓ 文档问答")

    # 文档选择（可选限定范围）
    docs: list[DocumentInfo] = st.session_state.get("documents", [])
    ready_docs = [d for d in docs if d.status == "ready"]

    if not ready_docs:
        st.info("暂无可用文档（状态为「就绪」），请先上传并等待处理完成。")
        return

    selected = st.multiselect(
        "限定文档范围（不选则查询全部就绪文档）",
        options=ready_docs,
        format_func=lambda d: d.original_name,
    )
    selected_ids = [d.id for d in selected] if selected else None

    question = st.text_area(
        "输入你的问题",
        placeholder="例如：这篇论文的核心方法是什么？",
        height=80,
    )

    if st.button("提问", type="primary", disabled=not question.strip()):
        try:
            with st.spinner("正在检索和生成答案…"):
                result = client.ask_question(question.strip(), document_ids=selected_ids)
            st.session_state["last_query_result"] = result
        except ApiClientError as exc:
            st.error(f"问答失败：{exc.detail}")
            st.session_state.pop("last_query_result", None)

    _render_query_result()


def _render_query_result() -> None:
    """渲染上一次问答的答案和引用。"""

    result: QueryResult | None = st.session_state.get("last_query_result")
    if result is None:
        return

    st.subheader("💡 答案")
    st.markdown(result.answer)
    st.caption(f"耗时 {result.elapsed_ms} ms | 请求 ID: `{result.request_id}`")

    if not result.citations:
        st.info("本次回答未附带引用。")
        return

    st.subheader(f"📎 引用详情（{len(result.citations)} 条）")
    for idx, cite in enumerate(result.citations, start=1):
        label = (
            f"[C{idx}] {cite.document_name} · 第 {cite.page_number} 页 · 片段 {cite.chunk_index}"
        )
        with st.expander(label, expanded=False):
            st.write(f"**来源文档**：{cite.document_name}（`{cite.document_id}`）")
            st.write(f"**页码**：第 {cite.page_number} 页")
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
