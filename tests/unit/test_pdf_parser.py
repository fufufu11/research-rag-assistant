"""PDF 解析器单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 1 验收）：
- 合法文本 PDF：正确解析每页，页码从 1 开始
- preview 长度上限：不超过 200 字
- 空 PDF（0 页）：抛 EmptyPdfError
- 损坏文件（非 PDF 内容）：抛 InvalidPdfError
- 不存在路径：抛 FileNotFoundError

测试 PDF 动态生成，不提交真实文档（PROJECT_PLAN.md 第 13.3 节）。
PyMuPDF 是本地库调用，不涉及外部模型 API，无需 Mock。

说明：测试文本用英文，因为 PyMuPDF 的 ``insert_text`` 默认字体（Helvetica）
不含中文字形，CI 环境（Linux）也不一定有中文字体。解析器本身对中文无特殊处理，
中文提取能力由 PyMuPDF 保证，不属于本解析器的测试范围。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pymupdf
import pytest

from research_rag.pdf_parser import (
    EmptyPdfError,
    InvalidPdfError,
    PageInfo,
    PdfParseResult,
    parse_pdf,
)

if TYPE_CHECKING:
    from pathlib import Path


def _build_empty_pdf_bytes() -> bytes:
    """构造一个最小的合法 0 页 PDF。

    PyMuPDF 的 ``save`` / ``tobytes`` 不允许保存 0 页文档（会抛
    ``ValueError: cannot save with zero pages``），所以这里按 PDF 规范
    手工拼接字节。xref 偏移由 Python 计算，避免手工数错。
    """
    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 /Kids [] >>\nendobj\n"

    obj1_offset = len(header)
    obj2_offset = obj1_offset + len(obj1)
    xref_offset = obj2_offset + len(obj2)

    xref = (
        b"xref\n"
        b"0 3\n"
        b"0000000000 65535 f \n"
        + f"{obj1_offset:010d} 00000 n \n".encode("ascii")
        + f"{obj2_offset:010d} 00000 n \n".encode("ascii")
    )

    trailer = (
        b"trailer\n<< /Size 3 /Root 1 0 R >>\n"
        b"startxref\n" + f"{xref_offset}\n".encode("ascii") + b"%%EOF"
    )

    return header + obj1 + obj2 + xref + trailer


@pytest.fixture
def two_page_pdf(tmp_path: Path) -> Path:
    """动态生成一个两页 PDF，每页包含英文文本。"""
    pdf_path = tmp_path / "sample.pdf"
    doc = pymupdf.open()  # 新建空文档
    try:
        page1 = doc.new_page()
        page1.insert_text((72, 72), "Page 1: Hello World")
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Page 2: PyMuPDF test")
        doc.save(pdf_path)
    finally:
        doc.close()
    return pdf_path


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """动态生成一个 0 页 PDF（合法但无内容）。"""
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(_build_empty_pdf_bytes())
    return pdf_path


@pytest.fixture
def corrupted_pdf(tmp_path: Path) -> Path:
    """生成一个内容不是 PDF 的 .pdf 文件（损坏文件）。"""
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_bytes(b"This is not a valid PDF file content.")
    return pdf_path


def test_parse_valid_pdf_returns_two_pages(two_page_pdf: Path) -> None:
    """合法 PDF 应解析出两页，页码从 1 开始，且包含插入的文本。"""
    result = parse_pdf(two_page_pdf)

    assert isinstance(result, PdfParseResult)
    assert result.page_count == 2
    assert len(result.pages) == 2

    page1 = result.pages[0]
    assert isinstance(page1, PageInfo)
    assert page1.page_number == 1
    assert page1.char_count > 0
    assert "Page 1" in page1.preview

    page2 = result.pages[1]
    assert page2.page_number == 2
    assert "Page 2" in page2.preview


def test_parse_valid_pdf_preview_max_200_chars(two_page_pdf: Path) -> None:
    """preview 不超过 200 字（阶段 1 验收要求）。"""
    result = parse_pdf(two_page_pdf)
    for page in result.pages:
        assert len(page.preview) <= 200


def test_parse_empty_pdf_raises_empty_pdf_error(empty_pdf: Path) -> None:
    """0 页 PDF 应抛 EmptyPdfError。"""
    with pytest.raises(EmptyPdfError):
        parse_pdf(empty_pdf)


def test_parse_corrupted_pdf_raises_invalid_pdf_error(corrupted_pdf: Path) -> None:
    """损坏文件应抛 InvalidPdfError。"""
    with pytest.raises(InvalidPdfError):
        parse_pdf(corrupted_pdf)


def test_parse_nonexistent_path_raises_file_not_found_error(tmp_path: Path) -> None:
    """不存在的路径应抛 FileNotFoundError。"""
    missing_path = tmp_path / "does_not_exist.pdf"
    with pytest.raises(FileNotFoundError):
        parse_pdf(missing_path)
