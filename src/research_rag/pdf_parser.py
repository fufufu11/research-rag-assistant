"""按页 PDF 解析器。

输入 PDF 文件路径，输出每页页码、字符数和前 200 字预览。
依据 PROJECT_PLAN.md 第 670 节（阶段 1 交付物与验收）。

设计取舍：
- 用 dataclass 而不是 dict：字段固定且类型明确，dataclass 自动生成
  ``__init__``/``__repr__``，既能在 IDE 中获得补全，也便于后续扩展字段。
- 用 ``pathlib.Path`` 而不是 ``str``：Path 是 ``os.PathLike`` 的实现，
  支持 ``/`` 拼接和 ``exists()`` 等方法，比裸字符串更安全。
- 自定义 ``InvalidPdfError`` / ``EmptyPdfError``：让业务层可以按错误类型
  决定如何响应（例如 API 层映射到 HTTP 400），而不是靠字符串匹配。
  文件不存在仍用内置 ``FileNotFoundError``，因为这是 Python 通用约定。
- 用 ``try/finally`` 关闭文档：即使解析中途抛异常，也能保证资源释放。
"""

from dataclasses import dataclass
from pathlib import Path

import pymupdf

# 每页预览的字符数上限，用于 CLI 输出和日志，避免过长。
# 阶段 1 验收要求"前 200 字"，中文字符在 Python 中 len() 按 1 计。
PREVIEW_LENGTH = 200


class InvalidPdfError(Exception):
    """文件不是可解析的文本型 PDF（损坏、格式错误等）。"""


class EmptyPdfError(Exception):
    """PDF 文件合法但没有页面（0 页）。"""


@dataclass(frozen=True)
class PageInfo:
    """单页解析结果。

    Attributes:
        page_number: 页码，从 1 开始（符合读者直觉，也是
            PROJECT_PLAN.md 第 7.2 节 Chunk.page_number 的约定）。
        char_count: 该页提取到的字符数（中文字符按 1 计）。
        preview: 该页文本的前 ``PREVIEW_LENGTH`` 字，用于 CLI 输出和日志预览。
    """

    page_number: int
    char_count: int
    preview: str


@dataclass(frozen=True)
class PdfParseResult:
    """整份 PDF 的解析结果。

    Attributes:
        pages: 每一页的解析结果，按页码升序排列。
        page_count: 总页数，等于 ``len(pages)``。
    """

    pages: list[PageInfo]
    page_count: int


def parse_pdf(path: Path) -> PdfParseResult:
    """按页解析 PDF 文件，返回每页页码、字符数和前 200 字预览。

    Args:
        path: PDF 文件路径。

    Returns:
        包含每页信息的解析结果。

    Raises:
        FileNotFoundError: 文件不存在。
        InvalidPdfError: 文件不是可解析的 PDF（损坏或格式错误）。
        EmptyPdfError: PDF 合法但没有页面。
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    # PyMuPDF 打开损坏文件会抛 pymupdf.FileDataError（Exception 子类），
    # 个别情况下也会抛 RuntimeError，统一映射为 InvalidPdfError 供业务层处理。
    # PyMuPDF 1.28 类型存根不完整，open/get_text/close 是 untyped function，
    # 在调用处用 type-ignore 注释精确抑制（warn_unused_ignores 会检查有效性）。
    try:
        doc = pymupdf.open(path)  # type: ignore[no-untyped-call]
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise InvalidPdfError(f"无法打开 PDF 文件: {path}") from exc

    # 用 try/finally 确保 doc.close() 一定执行，即使中途抛异常。
    try:
        if doc.page_count == 0:
            raise EmptyPdfError(f"PDF 文件没有页面: {path}")

        pages: list[PageInfo] = []
        for index in range(doc.page_count):
            page = doc[index]
            text = page.get_text()  # type: ignore[no-untyped-call]
            pages.append(
                PageInfo(
                    page_number=index + 1,
                    char_count=len(text),
                    preview=text[:PREVIEW_LENGTH],
                )
            )

        return PdfParseResult(pages=pages, page_count=doc.page_count)
    finally:
        doc.close()  # type: ignore[no-untyped-call]
