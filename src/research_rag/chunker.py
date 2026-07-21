"""页内文本清洗与带重叠的 Chunk 切分器。

依据 PROJECT_PLAN.md 第 678 节（阶段 2 交付物）、第 9.1 节（切分规则）。
使用 LangChain 的 ``RecursiveCharacterTextSplitter`` 按页切分，确保不跨页。

设计取舍：
- 按页调用 ``split_text``（而不是把整份文档拼成一个大字符串再切）：
  保证每个 Chunk 的 ``page_number`` 准确，引用溯源不会出错。代价是页与页
  之间没有重叠，但科研项目中跨页引用的代价更高，这个取舍值得。
- 用 ``RecursiveCharacterTextSplitter`` 而不是手写切分：
  它按分隔符层级（段落 → 句号 → 空格 → 字符）递归切分，尽量在语义边界
  断开，避免把词或句子从中间截断。分隔符列表加入了中文标点（``。！？；``），
  适配中文科研文献。
- ``clean_page_text`` 只做最小清洗（合并多余空白、统一换行）：不过度清洗
  导致公式编号和关键术语丢失。页眉页脚的过滤放到切分后的 ``min_chunk_chars``
  阈值处理，因为页眉页脚通常是切分后产生的短片段。
- ``Chunk`` 用 ``dataclass(frozen=True)``：与 ``PageInfo`` 一致，不可变，
  避免下游意外修改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_text_splitters import RecursiveCharacterTextSplitter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from research_rag.pdf_parser import PageInfo

# 默认参数（PROJECT_PLAN.md 第 9.1 节）
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80
# 片段最小字符数：低于此值的片段视为页眉/页脚或噪声，过滤掉。
# 20 字能过滤页码（"12"）、短页眉（"References"），但保留有意义的短句。
DEFAULT_MIN_CHUNK_CHARS = 20

# 中文友好的分隔符层级：段落 > 换行 > 中文句号 > 英文句号 > 中文分号 > 空格 > 字符
# 递归切分器会按顺序尝试这些分隔符，优先在靠前的分隔符处断开。
DEFAULT_SEPARATORS: list[str] = [
    "\n\n",  # 段落分隔
    "\n",  # 换行
    "。",  # 中文句号
    "！",  # 中文感叹号
    "？",  # 中文问号
    ".",  # 英文句号
    "!",  # 英文感叹号
    "?",  # 英文问号
    "；",  # 中文分号
    ";",  # 英文分号
    " ",  # 空格
    "",  # 兜底：按字符切
]


@dataclass(frozen=True)
class Chunk:
    """单个切分片段。

    Attributes:
        page_number: 原始页码，从 1 开始（与 ``PageInfo.page_number`` 一致）。
        chunk_index: 文档内分段序号，从 0 开始连续编号。
        content: 分段文本（已清洗）。
        char_count: ``content`` 的字符数（中文字符按 1 计）。
    """

    page_number: int
    chunk_index: int
    content: str
    char_count: int


@dataclass(frozen=True)
class ChunkerConfig:
    """切分器配置。

    Attributes:
        chunk_size: 每个片段的最大字符数。
        chunk_overlap: 相邻片段的重叠字符数（仅在页内生效，不跨页）。
        min_chunk_chars: 片段最小字符数，低于此值的片段被过滤。
    """

    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS


def clean_page_text(text: str) -> str:
    """清洗页内文本：最小化处理，保留内容完整性。

    只做三件事：
    1. 统一换行符（``\\r\\n`` / ``\\r`` → ``\\n``）
    2. 合并连续空格/制表符为单个空格
    3. 合并 3 个以上连续换行为 2 个（保留段落结构）

    不做：删除数字、删除短行、删除特殊字符。这些会误伤公式编号和术语。

    Args:
        text: 原始页面文本。

    Returns:
        清洗后的文本，首尾空白已去除。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 合并连续空格/制表符（但不合并换行，保留段落结构）
    text = re.sub(r"[ \t]+", " ", text)
    # 3+ 个换行合并为 2 个（段落分隔）
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_splitter(config: ChunkerConfig) -> RecursiveCharacterTextSplitter:
    """根据配置构造 LangChain 递归字符切分器。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        separators=DEFAULT_SEPARATORS,
        length_function=len,
        is_separator_regex=False,
    )


def chunk_pages(
    pages: Sequence[PageInfo],
    config: ChunkerConfig | None = None,
) -> list[Chunk]:
    """对 PDF 解析结果的每一页进行切分，返回保留页码和序号的 Chunk 列表。

    - 不跨页切分：每页独立调用 ``splitter.split_text``
    - 过滤极少字符的片段（页眉页脚等）
    - ``chunk_index`` 在文档内从 0 开始连续编号

    Args:
        pages: PDF 解析结果的页面列表（``PdfParseResult.pages``）。
        config: 切分配置，为 ``None`` 时使用默认值。

    Returns:
        切分后的 Chunk 列表，按页码升序、页内顺序排列。
    """
    if config is None:
        config = ChunkerConfig()

    splitter = _build_splitter(config)
    chunks: list[Chunk] = []
    chunk_index = 0

    for page in pages:
        cleaned = clean_page_text(page.text)
        if not cleaned:
            continue

        # 按页调用 split_text，保证不跨页
        pieces = splitter.split_text(cleaned)
        for piece in pieces:
            # 过滤极少字符的片段（页眉/页脚/噪声）
            if len(piece) < config.min_chunk_chars:
                continue
            chunks.append(
                Chunk(
                    page_number=page.page_number,
                    chunk_index=chunk_index,
                    content=piece,
                    char_count=len(piece),
                )
            )
            chunk_index += 1

    return chunks
