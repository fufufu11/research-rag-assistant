"""命令行入口：解析 PDF 并输出每页页码、字符数和前 200 字。

用法::

    uv run python scripts/parse_pdf.py <pdf_path>

退出码：
- 0：解析成功
- 2：文件不存在
- 3：文件不是可解析的 PDF（InvalidPdfError）
- 4：PDF 合法但没有页面（EmptyPdfError）
- 1：参数错误（argparse 默认行为）

退出码不与异常类型一一对应是刻意为之：便于 shell 脚本区分失败原因，
未来 API 层也会用类似的错误码映射（PROJECT_PLAN.md 第 8.5 节）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research_rag.pdf_parser import (
    EmptyPdfError,
    InvalidPdfError,
    parse_pdf,
)


def main(argv: list[str] | None = None) -> int:
    """解析 PDF 并打印每页信息。

    Args:
        argv: 命令行参数，默认为 None 时读取 sys.argv。

    Returns:
        退出码。
    """
    parser = argparse.ArgumentParser(
        description="按页解析 PDF，输出每页页码、字符数和前 200 字预览。",
    )
    parser.add_argument("pdf_path", type=Path, help="PDF 文件路径")
    args = parser.parse_args(argv)

    pdf_path: Path = args.pdf_path

    try:
        result = parse_pdf(pdf_path)
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except InvalidPdfError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 3
    except EmptyPdfError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 4

    print(f"文件: {pdf_path}")
    print(f"总页数: {result.page_count}")
    print("-" * 40)
    for page in result.pages:
        print(f"第 {page.page_number} 页 | 字符数: {page.char_count}")
        print(f"预览: {page.preview}")
        print("-" * 40)

    return 0


if __name__ == "__main__":
    sys.exit(main())
