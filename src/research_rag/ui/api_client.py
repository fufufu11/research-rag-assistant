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
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    request_id: str = ""
    elapsed_ms: int = 0


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
    """

    citations: list[Citation] = field(default_factory=list)
    request_id: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class StreamError:
    """流式错误事件：检索/LLM/证据不足等异常的 detail。"""

    detail: str


StreamEvent = StreamToken | StreamDone | StreamError
"""流式事件联合类型，``ask_question_stream`` 的产出单元。"""


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
    ) -> QueryResult:
        """提交问答请求（POST /queries）。

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 ID 列表。``None`` 或空列表表示查询全库。
            top_k: 检索返回的最相关片段数。``None`` 用 API 端默认值。

        Returns:
            问答结果（答案 + 引用列表）。
        """

        payload: dict[str, object] = {"question": question}
        if document_ids:
            payload["document_ids"] = document_ids
        if top_k is not None:
            payload["top_k"] = top_k

        response = self._request("POST", "/queries", json=payload)
        data = response.json()
        return QueryResult(
            answer=data.get("answer", ""),
            citations=[
                Citation(
                    document_id=c["document_id"],
                    document_name=c["document_name"],
                    start_page=c["start_page"],
                    end_page=c["end_page"],
                    chunk_index=c["chunk_index"],
                    snippet=c["snippet"],
                    score=c["score"],
                )
                for c in data.get("citations", [])
            ],
            request_id=data.get("request_id", ""),
            elapsed_ms=data.get("elapsed_ms", 0),
        )

    def ask_question_stream(
        self,
        question: str,
        document_ids: list[str] | None = None,
        top_k: int | None = None,
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
            top_k: 检索返回的最相关片段数。``None`` 用 API 端默认值。

        Yields:
            ``StreamEvent``：``StreamToken`` / ``StreamDone`` / ``StreamError``。
        """

        payload: dict[str, object] = {"question": question, "stream": True}
        if document_ids:
            payload["document_ids"] = document_ids
        if top_k is not None:
            payload["top_k"] = top_k

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
        # ``iter_lines`` 在 ``decode_unicode=True`` 下返回 str，但类型标注保留
        # ``bytes | str``，故做类型守卫。
        if not isinstance(raw_line, str):
            # 理论上 decode_unicode=True 不会走到这里，防御性处理
            raw_line = raw_line.decode("utf-8", errors="replace")  # type: ignore[union-attr]

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
        citations = [
            Citation(
                document_id=c["document_id"],
                document_name=c["document_name"],
                start_page=c["start_page"],
                end_page=c["end_page"],
                chunk_index=c["chunk_index"],
                snippet=c["snippet"],
                score=c["score"],
            )
            for c in data.get("citations", [])
        ]
        return StreamDone(
            citations=citations,
            request_id=str(data.get("request_id", "")),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
        )
    if event_name == "error":
        return StreamError(detail=str(data.get("detail", "未知错误")))
    raise ApiClientError(0, f"未知 SSE 事件类型：{event_name}")
