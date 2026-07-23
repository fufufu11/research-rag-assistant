"""FastAPI HTTP 客户端封装（供 Streamlit UI 层调用）。

依据 PROJECT_PLAN.md 第 13.6 节（分层：UI 层只调 API，不直接 import 业务层）、
第 8 节（API 草案：文档上传/列表/详情/删除 + 问答）。

设计取舍（初学者向说明）：
- **UI 层只调 HTTP API**：本模块封装所有对 FastAPI 的 HTTP 调用，Streamlit
  界面（``app.py``）只调用本模块的方法，不直接 import ``DocumentService`` /
  ``QaService``。理由：① 保持分层清晰（PROJECT_PLAN 第 13.6 节），前后端可
  独立部署；② UI 层不感知数据库、向量库、LLM 等内部实现；③ 便于单测——
  只需 mock ``requests`` 即可测试 UI 逻辑，无需启动真实 API 服务。
- **用 ``requests`` 而非 ``httpx``**：Streamlit 是同步执行模型，``requests``
  的同步 API 更直观；``httpx`` 虽然已作为 TestClient 传递依赖存在，但同步/
  异步 API 混用增加学习成本。
- **返回 dataclass 而非裸 dict**：``DocumentInfo`` / ``Citation`` / ``QueryResult``
  提供类型安全的字段访问，IDE 自动补全友好，比 dict 减少 key 拼写错误。
- **错误统一为 ``ApiClientError``**：HTTP 非 2xx 时抛 ``ApiClientError``（含
  status_code 和 detail），UI 层捕获后用 ``st.error`` 展示，无需每个调用点
  重复 try/except 不同异常。
- **``base_url`` 从环境变量读取**：默认 ``http://localhost:8000/api/v1``，
  可通过 ``API_BASE_URL`` 环境变量覆盖，部署时改环境变量即可，无需改代码。
- **``timeout`` 可配置**：默认 60 秒（问答可能涉及 LLM 调用，比一般 API 慢），
  避免长时间挂起。
- **流式问答（阶段 9.1）**：``ask_question_stream`` 用 ``requests stream=True``
  接收 SSE（``text/event-stream``），逐事件产出 ``StreamToken`` / ``StreamDone``
  / ``StreamError`` dataclass。UI 层用 ``st.write_stream`` 逐字渲染 token，
  ``done`` 事件后补充引用详情。SSE 解析按行迭代响应体，按
  ``event:`` / ``data:`` / 空行 切分事件块。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1"
# 问答接口可能触发 LLM 调用，默认超时放宽到 60 秒（LLM_BASE_URL 的 LLM_TIMEOUT
# 默认 30 秒 + 检索/Embedding 开销）。
DEFAULT_TIMEOUT = 60.0


class ApiClientError(Exception):
    """API 调用失败（HTTP 非 2xx 或网络错误）。

    Attributes:
        status_code: HTTP 状态码。网络错误（连接拒绝、超时）时为 0。
        detail: 错误详情。HTTP 错误时取响应体的 ``detail`` 字段，网络错误时
            为异常消息。
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}" if status_code else detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class DocumentInfo:
    """文档信息（对应 API 的 ``DocumentRead`` schema）。

    仅保留 UI 展示需要的字段，省略 ``sha256`` / ``stored_name`` 等内部字段。
    """

    id: str
    original_name: str
    status: str
    page_count: int
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Citation:
    """单条引用（对应 API 的 ``CitationRead`` schema）。

    Attributes:
        document_id: 来源文档 UUID。
        document_name: 来源文档名。
        start_page: chunk 内容起始页码。
        end_page: chunk 内容结束页码。跨页切分时 ``end_page > start_page``，
            不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号。
        snippet: 原文片段。
        score: 检索相似度分数。
    """

    document_id: str
    document_name: str
    start_page: int
    end_page: int
    chunk_index: int
    snippet: str
    score: float


@dataclass(frozen=True)
class QueryResult:
    """问答结果（对应 API 的 ``QueryResponse`` schema）。

    Attributes:
        answer: 模型生成的答案文本（含 ``[C1]`` 等引用标记）。
        citations: 引用列表，按模型引用顺序排列。
        request_id: 本次问答的唯一 ID，便于日志追踪。
        elapsed_ms: 本次问答总耗时（毫秒）。
        conversation_id: 本次问答所属会话 ID（阶段 9.2）。``None`` 表示单轮
            问答未关联会话；非 None 时前端据此维护会话状态。
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    request_id: str = ""
    elapsed_ms: int = 0
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# 流式事件（阶段 9.1 SSE）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamToken:
    """流式 token 事件：一段 LLM 生成的文本片段。

    ``text`` 是答案原文（含 ``[C1]`` 等引用标记），前端逐字拼接渲染；
    引用元数据由 ``StreamDone`` 在流结束后统一下发。
    """

    text: str


@dataclass(frozen=True)
class StreamDone:
    """流式完成事件：携带服务端映射后的引用与耗时元数据。

    Attributes:
        citations: 服务端根据答案中的 ``[C1]`` 编号映射的真实引用列表。
        request_id: 本次问答的唯一 ID。
        elapsed_ms: 本次问答总耗时（毫秒）。
        conversation_id: 本次问答所属会话 ID（阶段 9.2）。``None`` 表示单轮
            问答未关联会话；非 None 时前端据此维护会话状态。
    """

    citations: list[Citation] = field(default_factory=list)
    request_id: str = ""
    elapsed_ms: int = 0
    conversation_id: str | None = None


@dataclass(frozen=True)
class StreamError:
    """流式错误事件：检索/LLM/证据不足等异常的 detail。"""

    detail: str


StreamEvent = StreamToken | StreamDone | StreamError
"""流式事件联合类型，``ask_question_stream`` 的产出单元。"""


# ---------------------------------------------------------------------------
# 会话与消息（阶段 9.2 多轮对话）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageInfo:
    """会话消息（对应 API 的 ``MessageRead`` schema）。

    Attributes:
        id: 消息 UUID。
        role: 消息角色（``user`` / ``assistant``）。
        content: 消息文本。``assistant`` 消息含 ``[C1]`` 等引用标记原文。
        citations: ``assistant`` 消息的引用元数据快照；``user`` 消息为 ``None``。
        created_at: 创建时间（ISO 字符串）。
    """

    id: str
    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: str = ""


@dataclass(frozen=True)
class ConversationInfo:
    """会话信息（对应 API 的 ``ConversationRead`` schema）。

    Attributes:
        id: 会话 UUID。
        title: 会话标题（可空）。
        document_ids: 会话级文档范围（UUID 字符串列表快照）；``None`` 表示全库。
        created_at: 创建时间（ISO 字符串）。
        updated_at: 最后更新时间（ISO 字符串）。
        messages: 会话内消息列表（按 ``created_at`` 升序）。``None`` 表示未加载
            （列表接口为节省体积不返回消息，详情接口返回完整消息）。
    """

    id: str
    title: str | None
    document_ids: list[str] | None = None
    created_at: str = ""
    updated_at: str = ""
    messages: list[MessageInfo] | None = None


class ApiClient:
    """FastAPI HTTP 客户端封装。

    所有方法对应一个 API 端点，返回 dataclass，失败时抛 ``ApiClientError``。
    UI 层（``app.py``）只调用本类的方法，不直接 import 业务层。

    Example:
        >>> client = ApiClient()
        >>> docs = client.list_documents()
        >>> result = client.ask_question("这篇论文的主题是什么？")
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: API 基地址。``None`` 时从环境变量 ``API_BASE_URL`` 读取，
                未设置时用 ``DEFAULT_API_BASE_URL``。
            timeout: 请求超时秒数。``None`` 时用 ``DEFAULT_TIMEOUT``。
        """

        self.base_url = (base_url or os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)).rstrip(
            "/"
        )
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    def _request(
        self,
        method: str,
        path: str,
        *,
        files: Any = None,
        json: Any = None,
    ) -> requests.Response:
        """发送 HTTP 请求，返回 Response。非 2xx 时抛 ``ApiClientError``。

        网络错误（连接拒绝、超时）也统一包装为 ``ApiClientError``（status_code=0）。
        """

        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method=method,
                url=url,
                files=files,
                json=json,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiClientError(0, f"无法连接 API 服务（{self.base_url}）：{exc}") from exc

        if not response.ok:
            detail = _extract_detail(response)
            raise ApiClientError(response.status_code, detail)
        return response

    def upload_document(self, file_bytes: bytes, filename: str) -> DocumentInfo:
        """上传 PDF 文档（POST /documents）。

        Args:
            file_bytes: PDF 文件二进制内容。
            filename: 文件名（用于 API 端的 original_name）。

        Returns:
            文档信息。重复上传抛 ``ApiClientError``（409）。
        """

        response = self._request(
            "POST",
            "/documents",
            files={"file": (filename, file_bytes, "application/pdf")},
        )
        return _parse_document(response.json())

    def list_documents(self) -> list[DocumentInfo]:
        """获取文档列表（GET /documents），按创建时间降序。"""

        response = self._request("GET", "/documents")
        data = response.json()
        items = data.get("items", []) if isinstance(data, dict) else data
        return [_parse_document(item) for item in items]

    def get_document(self, doc_id: str) -> DocumentInfo:
        """获取文档详情（GET /documents/{doc_id}）。不存在抛 404。"""

        response = self._request("GET", f"/documents/{doc_id}")
        return _parse_document(response.json())

    def delete_document(self, doc_id: str) -> None:
        """删除文档（DELETE /documents/{doc_id}）。不存在抛 404。"""

        self._request("DELETE", f"/documents/{doc_id}")

    def ask_question(
        self,
        question: str,
        document_ids: list[str] | None = None,
        top_k: int | None = None,
        conversation_id: str | None = None,
    ) -> QueryResult:
        """提交问答请求（POST /queries）。

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 ID 列表。``None`` 或空列表表示查询全库。
                ``conversation_id`` 非 None 且会话已锁定 ``document_ids`` 时，
                以会话锁定范围为准（API 端处理，客户端只透传）。
            top_k: 检索返回的最相关片段数。``None`` 用 API 端默认值。
            conversation_id: 会话 ID（阶段 9.2）。``None`` 表示单轮问答；
                传入已存在会话 ID 时，API 端加载历史注入 prompt 并持久化本轮消息。

        Returns:
            问答结果（答案 + 引用列表 + conversation_id）。
        """

        payload: dict[str, object] = {"question": question}
        if document_ids:
            payload["document_ids"] = document_ids
        if top_k is not None:
            payload["top_k"] = top_k
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id

        response = self._request("POST", "/queries", json=payload)
        data = response.json()
        return QueryResult(
            answer=data.get("answer", ""),
            citations=[
                _parse_citation(c) for c in data.get("citations", []) if isinstance(c, dict)
            ],
            request_id=data.get("request_id", ""),
            elapsed_ms=data.get("elapsed_ms", 0),
            conversation_id=data.get("conversation_id"),
        )

    def ask_question_stream(
        self,
        question: str,
        document_ids: list[str] | None = None,
        top_k: int | None = None,
        conversation_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        """流式问答请求（POST /queries with ``stream=true``）。

        用 ``requests`` 的流式响应接收 SSE（``text/event-stream``），逐事件
        产出 ``StreamToken`` / ``StreamDone`` / ``StreamError``。UI 层用
        ``st.write_stream`` 逐字渲染 token，``done`` 后补充引用详情。

        连接失败或 HTTP 非 2xx 时抛 ``ApiClientError``（在首次迭代时触发，
        因为生成器函数体到迭代才开始执行）；流中业务异常转为 ``StreamError``
        事件，不抛出。

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 ID 列表。``None`` 或空列表表示查询全库。
                ``conversation_id`` 非 None 且会话已锁定 ``document_ids`` 时，
                以会话锁定范围为准（API 端处理，客户端只透传）。
            top_k: 检索返回的最相关片段数。``None`` 用 API 端默认值。
            conversation_id: 会话 ID（阶段 9.2）。``None`` 表示单轮问答。

        Yields:
            ``StreamEvent``：``StreamToken`` / ``StreamDone`` / ``StreamError``。
            ``StreamDone`` 携带 ``conversation_id`` 字段。
        """

        payload: dict[str, Any] = {"question": question, "stream": True}
        if document_ids:
            payload["document_ids"] = document_ids
        if top_k is not None:
            payload["top_k"] = top_k
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id

        url = f"{self.base_url}/queries"
        try:
            response = requests.post(
                url=url,
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise ApiClientError(0, f"无法连接 API 服务（{self.base_url}）：{exc}") from exc

        if not response.ok:
            detail = _extract_detail(response)
            raise ApiClientError(response.status_code, detail)

        yield from _parse_sse_stream(response)

    # ------------------------------------------------------------------
    # 会话管理（阶段 9.2 多轮对话）
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        title: str | None = None,
        document_ids: list[str] | None = None,
    ) -> ConversationInfo:
        """创建会话（POST /conversations）。

        Args:
            title: 会话标题（可空）。未提供时 API 端在首条消息后用问题截取自动设置。
            document_ids: 会话级文档范围锁定（UUID 字符串列表）。``None`` 或空
                列表表示查询全库 READY 文档。锁定后后续问答以此范围为准。

        Returns:
            新建的会话信息（无消息，``messages`` 为 ``None``）。
        """

        payload: dict[str, object] = {}
        if title is not None:
            payload["title"] = title
        if document_ids:
            payload["document_ids"] = document_ids
        response = self._request("POST", "/conversations", json=payload)
        return _parse_conversation(response.json())

    def list_conversations(self) -> list[ConversationInfo]:
        """列出所有会话（GET /conversations），按 ``updated_at`` 降序。

        列表接口不返回 ``messages``（节省体积），需要消息列表请用
        ``get_conversation``。
        """

        response = self._request("GET", "/conversations")
        data = response.json()
        items = data.get("items", []) if isinstance(data, dict) else data
        return [_parse_conversation(item) for item in items]

    def get_conversation(self, conversation_id: str) -> ConversationInfo:
        """查询会话详情（GET /conversations/{id}），含完整消息列表。

        不存在抛 ``ApiClientError``（404）。
        """

        response = self._request("GET", f"/conversations/{conversation_id}")
        return _parse_conversation(response.json())

    def delete_conversation(self, conversation_id: str) -> None:
        """删除会话（DELETE /conversations/{id}），级联删除其消息。

        不存在抛 ``ApiClientError``（404）。
        """

        self._request("DELETE", f"/conversations/{conversation_id}")

    def list_messages(self, conversation_id: str) -> list[MessageInfo]:
        """列出会话内消息（GET /conversations/{id}/messages），按时间升序。

        独立端点便于在不重新拉取整个会话详情的情况下刷新消息列表。
        不存在抛 ``ApiClientError``（404）。
        """

        response = self._request("GET", f"/conversations/{conversation_id}/messages")
        data = response.json()
        items = data if isinstance(data, list) else data.get("items", [])
        return [_parse_message(item) for item in items]


def _parse_document(data: Mapping[str, Any]) -> DocumentInfo:
    """从 API 响应 JSON 解析 ``DocumentInfo``。"""

    error_message = data.get("error_message")
    return DocumentInfo(
        id=str(data["id"]),
        original_name=str(data["original_name"]),
        status=str(data["status"]),
        page_count=int(data["page_count"]),
        error_message=error_message if isinstance(error_message, str) else None,
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )


def _parse_citation(data: Mapping[str, Any]) -> Citation:
    """从 API 响应 JSON 解析单条 ``Citation``。"""

    return Citation(
        document_id=str(data["document_id"]),
        document_name=str(data["document_name"]),
        start_page=int(data["start_page"]),
        end_page=int(data["end_page"]),
        chunk_index=int(data["chunk_index"]),
        snippet=str(data["snippet"]),
        score=float(data["score"]),
    )


def _parse_message(data: Mapping[str, Any]) -> MessageInfo:
    """从 API 响应 JSON 解析 ``MessageInfo``。"""

    citations_raw = data.get("citations")
    citations = (
        [_parse_citation(c) for c in citations_raw if isinstance(c, dict)]
        if isinstance(citations_raw, list)
        else None
    )
    return MessageInfo(
        id=str(data["id"]),
        role=str(data["role"]),
        content=str(data["content"]),
        citations=citations,
        created_at=str(data.get("created_at", "")),
    )


def _parse_conversation(data: Mapping[str, Any]) -> ConversationInfo:
    """从 API 响应 JSON 解析 ``ConversationInfo``。"""

    document_ids = data.get("document_ids")
    messages_raw = data.get("messages")
    messages = (
        [_parse_message(m) for m in messages_raw if isinstance(m, dict)]
        if isinstance(messages_raw, list)
        else None
    )
    return ConversationInfo(
        id=str(data["id"]),
        title=data.get("title"),
        document_ids=([str(d) for d in document_ids] if isinstance(document_ids, list) else None),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        messages=messages,
    )


def _extract_detail(response: requests.Response) -> str:
    """从错误响应中提取 detail 字段，无则返回原始文本。"""

    try:
        body = response.json()
    except ValueError:
        return response.text or "未知错误"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return response.text or "未知错误"


def _parse_sse_stream(response: requests.Response) -> Iterator[StreamEvent]:
    """解析 SSE 流（``text/event-stream``），逐事件产出 ``StreamEvent``。

    SSE 事件块格式（``\\n\\n`` 分隔）::

        event: <name>
        data: <json>

    按行迭代响应体，累积 ``event:`` 和 ``data:`` 行，遇空行表示事件结束，
    解析并产出对应 ``StreamEvent``。支持多行 ``data:``（用 ``\\n`` 拼接）。

    未知事件类型抛 ``ApiClientError``（协议不一致应尽早暴露）。
    """

    event_name = ""
    data_lines: list[str] = []

    def _flush_current() -> Iterator[StreamEvent]:
        """产出当前累积的事件并重置缓冲（闭包共享外层变量）。"""

        nonlocal event_name, data_lines
        if event_name and data_lines:
            yield _build_stream_event(event_name, "\n".join(data_lines))
        event_name = ""
        data_lines = []

    for raw_line in response.iter_lines(decode_unicode=True):
        # ``iter_lines`` 类型标注为 ``Iterator[bytes | str]``，``decode_unicode=True``
        # 运行时返回 str，但存根未精确反映，故做类型守卫。
        if not isinstance(raw_line, str):
            # 理论上 decode_unicode=True 不会走到这里，防御性处理
            raw_line = raw_line.decode("utf-8", errors="replace")

        if raw_line == "":
            # 空行：事件结束
            yield from _flush_current()
        elif raw_line.startswith("event:"):
            # 遇到新事件块：先 flush 前一个（容错：流可能无空行分隔）
            if event_name or data_lines:
                yield from _flush_current()
            event_name = raw_line[len("event:") :].strip()
        elif raw_line.startswith("data:"):
            data_lines.append(raw_line[len("data:") :].strip())
        # 其他行（如 ``:`` 开头的注释）忽略

    # 流结束时若仍有未发的事件（无末尾空行的容错），也产出
    if event_name and data_lines:
        yield _build_stream_event(event_name, "\n".join(data_lines))


def _build_stream_event(event_name: str, data_str: str) -> StreamEvent:
    """根据事件名和 data JSON 构建 ``StreamEvent``。"""

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError as exc:
        raise ApiClientError(0, f"SSE 事件数据解析失败（{event_name}）：{exc}") from exc

    if event_name == "token":
        return StreamToken(text=str(data.get("text", "")))
    if event_name == "done":
        citations = [_parse_citation(c) for c in data.get("citations", []) if isinstance(c, dict)]
        conversation_id_raw = data.get("conversation_id")
        return StreamDone(
            citations=citations,
            request_id=str(data.get("request_id", "")),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            conversation_id=(str(conversation_id_raw) if conversation_id_raw is not None else None),
        )
    if event_name == "error":
        return StreamError(detail=str(data.get("detail", "未知错误")))
    raise ApiClientError(0, f"未知 SSE 事件类型：{event_name}")
