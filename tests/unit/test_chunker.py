"""文本切分器单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 2 验收）：
- 单页短文本：1 个 chunk，页码和序号正确
- 单页长文本：多个 chunk，页码一致，序号连续，长度不超 chunk_size
- 多页：页码正确，序号跨页连续，不跨页切分
- 重叠：同页相邻 chunk 共享部分内容
- 过滤：极少字符片段被过滤
- 自定义配置：参数生效
- 边界：空页列表、空白页面、文本刚好等于 chunk_size
- clean_page_text：合并多余空白、统一换行

测试直接构造 PageInfo 实例，不依赖 PDF 解析（保持单元测试独立性）。
LangChain 的 RecursiveCharacterTextSplitter 是本地纯计算，不涉及外部 API，无需 Mock。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from research_rag.chunker import (
    Chunk,
    ChunkerConfig,
    chunk_pages,
    clean_page_text,
)
from research_rag.pdf_parser import PageInfo

if TYPE_CHECKING:
    from collections.abc import Sequence


def _make_page(page_number: int, text: str) -> PageInfo:
    """构造 PageInfo 实例用于测试，preview 取前 200 字。"""
    return PageInfo(
        page_number=page_number,
        char_count=len(text),
        text=text,
        preview=text[:200],
    )


def test_short_text_single_chunk() -> None:
    """单页短文本（< chunk_size 但 >= min_chunk_chars）应切分为 1 个 chunk。"""
    # 文本需 >= min_chunk_chars（默认 20）才不会被过滤
    page = _make_page(1, "这是一段简短的文本，不足以触发切分器执行。")
    chunks = chunk_pages([page])

    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, Chunk)
    assert chunk.page_number == 1
    assert chunk.chunk_index == 0
    assert chunk.content == "这是一段简短的文本，不足以触发切分器执行。"
    assert chunk.char_count == len(chunk.content)


def test_long_text_multiple_chunks_same_page() -> None:
    """单页长文本应切分为多个 chunk，页码一致，序号连续，长度不超 chunk_size。"""
    # 构造一段远超 chunk_size 的文本，用句号分隔的句子组成
    sentence = "这是一个用于测试切分器的句子，长度适中。" * 5  # 约 100 字
    long_text = sentence * 10  # 约 1000 字，超过默认 chunk_size=500
    page = _make_page(3, long_text)

    chunks = chunk_pages([page])

    assert len(chunks) > 1
    # 所有 chunk 页码一致
    assert all(c.page_number == 3 for c in chunks)
    # 序号从 0 开始连续
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
    # 每个 chunk 长度不超过 chunk_size（RecursiveCharacterTextSplitter 的契约）
    config = ChunkerConfig()
    for chunk in chunks:
        assert chunk.char_count <= config.chunk_size


def test_multi_page_chunk_index_continuous_and_page_number_correct() -> None:
    """多页文档：页码正确，序号跨页连续。"""
    # 每页文本需 > chunk_size 才会切出多个 chunk
    sentence = "这是第一页的测试句子，长度适中。" * 40  # 约 640 字，超过 chunk_size=500
    page1 = _make_page(1, sentence)
    page2 = _make_page(2, sentence)
    page3 = _make_page(3, sentence)

    chunks = chunk_pages([page1, page2, page3])

    assert len(chunks) > 3  # 每页至少切出多个
    # 序号从 0 开始连续
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
    # 页码升序：page 1 的 chunk 在前，page 3 的 chunk 在后
    page_numbers = [c.page_number for c in chunks]
    assert page_numbers == sorted(page_numbers)
    assert 1 in page_numbers
    assert 2 in page_numbers
    assert 3 in page_numbers


def test_no_cross_page_split() -> None:
    """不跨页切分：page 1 的标记不出现在 page 2 的任何 chunk 中，反之亦然。"""
    page1 = _make_page(1, "PAGE_ONE_MARKER " + "句子内容。" * 100)
    page2 = _make_page(2, "PAGE_TWO_MARKER " + "句子内容。" * 100)

    chunks = chunk_pages([page1, page2])

    page1_chunks = [c for c in chunks if c.page_number == 1]
    page2_chunks = [c for c in chunks if c.page_number == 2]
    assert len(page1_chunks) > 0
    assert len(page2_chunks) > 0

    # page 1 的标记至少出现在 page 1 的某个 chunk 中
    assert any("PAGE_ONE_MARKER" in c.content for c in page1_chunks)
    # page 1 的标记不出现在 page 2 的任何 chunk 中
    assert all("PAGE_ONE_MARKER" not in c.content for c in page2_chunks)
    # page 2 的标记至少出现在 page 2 的某个 chunk 中
    assert any("PAGE_TWO_MARKER" in c.content for c in page2_chunks)
    # page 2 的标记不出现在 page 1 的任何 chunk 中
    assert all("PAGE_TWO_MARKER" not in c.content for c in page1_chunks)


def test_overlap_between_adjacent_chunks_same_page() -> None:
    """同页相邻 chunk 应共享部分内容（重叠区）。"""
    # 构造一段没有段落分隔的长文本，迫使切分器在句号处切分并产生重叠
    sentence = "这是一个完整的句子。" * 60  # 约 600 字，超过 chunk_size=500
    page = _make_page(1, sentence)

    config = ChunkerConfig(chunk_size=200, chunk_overlap=50)
    chunks = chunk_pages([page], config)

    assert len(chunks) >= 2
    # 相邻 chunk 的末尾与开头应有重叠内容
    # RecursiveCharacterTextSplitter 的重叠是近似的，验证存在共享子串即可
    for i in range(len(chunks) - 1):
        current = chunks[i].content
        next_content = chunks[i + 1].content
        # 取当前 chunk 的最后 30 字，验证它出现在下一 chunk 中
        tail = current[-30:]
        assert tail in next_content or next_content[:30] in current


def test_short_fragments_filtered() -> None:
    """少于 min_chunk_chars 的片段应被过滤。"""
    # 页面由一个短噪声和一段正常文本组成
    text = "页眉\n\n" + "这是正文内容，长度足够通过过滤阈值。" * 5
    page = _make_page(1, text)

    config = ChunkerConfig(min_chunk_chars=20)
    chunks = chunk_pages([page], config)

    # 所有保留的 chunk 都应超过 min_chunk_chars
    for chunk in chunks:
        assert chunk.char_count >= config.min_chunk_chars
    # 不应包含孤立的 "页眉" 片段
    for chunk in chunks:
        assert chunk.content.strip() != "页眉"


def test_custom_config_takes_effect() -> None:
    """自定义配置（更小的 chunk_size）应产生更多 chunk。"""
    sentence = "这是一个用于测试的句子。" * 40  # 约 400 字
    page = _make_page(1, sentence)

    large_config = ChunkerConfig(chunk_size=500, chunk_overlap=80)
    small_config = ChunkerConfig(chunk_size=100, chunk_overlap=20)

    large_chunks = chunk_pages([page], large_config)
    small_chunks = chunk_pages([page], small_config)

    # 小 chunk_size 应产生更多片段
    assert len(small_chunks) > len(large_chunks)
    # 小 chunk_size 的片段长度更短
    assert small_chunks[0].char_count <= 100


def test_empty_pages_returns_empty_list() -> None:
    """空页面列表应返回空 chunk 列表。"""
    chunks: Sequence[Chunk] = chunk_pages([])
    assert chunks == []


def test_whitespace_only_page_produces_no_chunks() -> None:
    """只有空白的页面不应产生任何 chunk。"""
    page = _make_page(1, "   \n\n   \t  \n\n  ")
    chunks = chunk_pages([page])
    assert chunks == []


def test_char_count_matches_content_length() -> None:
    """每个 chunk 的 char_count 应等于 len(content)。"""
    sentence = "测试句子。" * 80
    page = _make_page(1, sentence)
    chunks = chunk_pages([page])
    for chunk in chunks:
        assert chunk.char_count == len(chunk.content)


def test_clean_page_text_normalizes_whitespace() -> None:
    """clean_page_text 应合并连续空格、统一换行、合并多余空行。"""
    raw = "  多个   空格\r\n\r\n\r\n  换行  \r\n尾部  "
    cleaned = clean_page_text(raw)
    # 首尾空白去除
    assert not cleaned.startswith(" ")
    assert not cleaned.endswith(" ")
    # 连续空格合并为单个
    assert "   " not in cleaned
    # \r\n 统一为 \n
    assert "\r" not in cleaned
    # 3+ 换行合并为 2 个
    assert "\n\n\n" not in cleaned


def test_clean_page_text_preserves_content() -> None:
    """clean_page_text 不应删除公式编号和关键术语。"""
    raw = "公式 (1) 表示损失函数\n\n公式 (2) 表示梯度\n\nL = -y log(p)"
    cleaned = clean_page_text(raw)
    assert "(1)" in cleaned
    assert "(2)" in cleaned
    assert "L = -y log(p)" in cleaned


def test_default_config_values() -> None:
    """默认配置应符合 PROJECT_PLAN 第 9.1 节。"""
    config = ChunkerConfig()
    assert config.chunk_size == 500
    assert config.chunk_overlap == 80


def test_text_exactly_at_chunk_size_boundary() -> None:
    """文本长度刚好接近 chunk_size 时应正常切分，不崩溃。"""
    # 构造长度接近 chunk_size 的文本
    text = "字" * 500
    page = _make_page(1, text)
    chunks = chunk_pages([page], ChunkerConfig(chunk_size=500, chunk_overlap=80))
    # 不崩溃即可，长度不超 chunk_size
    for chunk in chunks:
        assert chunk.char_count <= 500
