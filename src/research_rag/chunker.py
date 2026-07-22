"""页内文本清洗与带重叠的 Chunk 切分器（支持跨页切分）。

依据 PROJECT_PLAN.md 第 678 节（阶段 2 交付物）、第 9.1 节（切分规则）、
docs/ROADMAP.md 阶段 8.2（跨页切分）。

设计取舍：
- ``cross_page=True``（默认）：先按页提取文本，直接拼接为一个大字符串
  （不添加页间分隔符），再用 ``RecursiveCharacterTextSplitter`` 统一切分。
  不用 ``\\n\\n`` 作页间分隔符是因为它也是首个切分分隔符，会导致每页独立
  切分而无法跨页。好处是跨页的段落/句子不会被切断。代价是 chunk 可能跨越
  多页，需要用 ``start_page`` + ``end_page`` 记录页码范围。
- ``cross_page=False``：保持旧行为（按页独立切分），仅供评测脚本 A/B 对比。
- 页码溯源用字符偏移追踪：合并时记录每页在合并文本中的起始偏移，切分后用
  ``str.find`` 定位每个 chunk 的位置，映射到 ``[start_page, end_page]`` 范围。
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

# 用于检测 chunk 内容前导分隔符的字符集合（overlap 可能导致 chunk 以分隔符开头）
# 这些是 DEFAULT_SEPARATORS 中的单字符分隔符；lstrip 会自动处理多字符序列（如 "\n\n"）
_SEPARATOR_CHARS = "。！？.!?；;\n "


@dataclass(frozen=True)
class Chunk:
    """单个切分片段。

    Attributes:
        start_page: chunk 内容起始页码，从 1 开始（与 ``PageInfo.page_number`` 一致）。
        end_page: chunk 内容结束页码。不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号，从 0 开始连续编号。
        content: 分段文本（已清洗）。
        char_count: ``content`` 的字符数（中文字符按 1 计）。
    """

    start_page: int
    end_page: int
    chunk_index: int
    content: str
    char_count: int


@dataclass(frozen=True)
class ChunkerConfig:
    """切分器配置。

    Attributes:
        chunk_size: 每个片段的最大字符数。
        chunk_overlap: 相邻片段的重叠字符数。
        min_chunk_chars: 片段最小字符数，低于此值的片段被过滤。
        cross_page: 是否跨页切分。``True`` 时合并连续页文本后统一切分
            （默认，阶段 8.2）；``False`` 时按页独立切分（旧行为，评测对比用）。
    """

    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS
    cross_page: bool = True


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


def _find_page_for_offset(page_starts: list[tuple[int, int]], offset: int) -> int:
    """根据字符偏移找到所属页码。

    ``page_starts`` 是 ``(offset, page_number)`` 列表，按 offset 升序排列。
    返回最后一个 ``offset <= 给定偏移`` 的页码。

    Args:
        page_starts: 页码起始偏移列表。
        offset: 要查找的字符偏移。

    Returns:
        该偏移所属的页码。
    """
    result = page_starts[0][1]
    for start_offset, page_num in page_starts:
        if start_offset <= offset:
            result = page_num
        else:
            break
    return result


def chunk_pages(
    pages: Sequence[PageInfo],
    config: ChunkerConfig | None = None,
) -> list[Chunk]:
    """对 PDF 解析结果进行切分，返回保留页码范围和序号的 Chunk 列表。

    - ``cross_page=True``（默认）：合并连续页文本后统一切分，chunk 可跨页
    - ``cross_page=False``：按页独立切分（旧行为）
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

    if config.cross_page:
        return _chunk_pages_cross_page(pages, config)
    return _chunk_pages_per_page(pages, config)


def _chunk_pages_per_page(
    pages: Sequence[PageInfo],
    config: ChunkerConfig,
) -> list[Chunk]:
    """按页独立切分（旧行为，cross_page=False 时使用）。

    每页独立调用 ``split_text``，不跨页切分。每个 chunk 的
    ``start_page == end_page``。
    """
    splitter = _build_splitter(config)
    chunks: list[Chunk] = []
    chunk_index = 0

    for page in pages:
        cleaned = clean_page_text(page.text)
        if not cleaned:
            continue

        pieces = splitter.split_text(cleaned)
        for piece in pieces:
            if len(piece) < config.min_chunk_chars:
                continue
            chunks.append(
                Chunk(
                    start_page=page.page_number,
                    end_page=page.page_number,
                    chunk_index=chunk_index,
                    content=piece,
                    char_count=len(piece),
                )
            )
            chunk_index += 1

    return chunks


def _chunk_pages_cross_page(
    pages: Sequence[PageInfo],
    config: ChunkerConfig,
) -> list[Chunk]:
    """跨页切分：合并连续页文本后统一切分。

    流程：
    1. 清洗每页文本，跳过空页
    2. 直接拼接为一个大字符串（不添加页间分隔符），记录每页的起始偏移。
       不用 ``\\n\\n`` 作页间分隔符是因为它也是 ``DEFAULT_SEPARATORS`` 的首个
       分隔符，会导致 splitter 在页边界处先切分，使每页独立切分，无法产生
       跨页 chunk。页内段落分隔（``\\n\\n``）仍由 ``clean_page_text`` 保留。
    3. 用 ``RecursiveCharacterTextSplitter`` 切分合并文本
    4. 对每个 chunk 用 ``str.find`` 定位其在合并文本中的位置
    5. 跳过 chunk 前导分隔符（overlap 可能导致 chunk 以分隔符开头），
       用第一个实质内容字符的位置计算 ``start_page``
    6. 根据位置映射到 ``[start_page, end_page]`` 页码范围
    7. 过滤极短片段
    """
    splitter = _build_splitter(config)

    # 构建合并文本和页码偏移表
    page_starts: list[tuple[int, int]] = []  # (offset, page_number)
    parts: list[str] = []
    offset = 0

    for page in pages:
        cleaned = clean_page_text(page.text)
        if not cleaned:
            continue
        page_starts.append((offset, page.page_number))
        parts.append(cleaned)
        offset += len(cleaned)

    if not parts:
        return []

    merged = "".join(parts)
    pieces = splitter.split_text(merged)

    chunks: list[Chunk] = []
    chunk_index = 0
    search_pos = 0
    for piece in pieces:
        if len(piece) < config.min_chunk_chars:
            continue

        # 在合并文本中定位 chunk 的位置
        pos = merged.find(piece, search_pos)
        if pos == -1:
            # 从头搜索（重叠可能导致 search_pos 过前）
            pos = merged.find(piece)

        if pos == -1:
            # 极端情况：无法定位（不应发生），分配到最后一页
            start_page = page_starts[-1][1] if page_starts else 1
            end_page = start_page
        else:
            end_pos = pos + len(piece) - 1
            # 处理 overlap 导致的前导分隔符：
            # chunk 可能以分隔符开头（来自前一 chunk 末尾的 overlap），这些分隔符
            # 可能属于前一页末尾，导致 start_page 错误地映射到前一页。
            # 跳过前导分隔符，用第一个实质内容字符的位置计算 start_page。
            leading_sep_len = len(piece) - len(piece.lstrip(_SEPARATOR_CHARS))
            content_start = pos + leading_sep_len
            start_page = _find_page_for_offset(page_starts, content_start)
            end_page = _find_page_for_offset(page_starts, end_pos)
            # 前进搜索位置（+1 保证能找到重叠的下一个 chunk）
            search_pos = pos + 1

        chunks.append(
            Chunk(
                start_page=start_page,
                end_page=end_page,
                chunk_index=chunk_index,
                content=piece,
                char_count=len(piece),
            )
        )
        chunk_index += 1

    return chunks
