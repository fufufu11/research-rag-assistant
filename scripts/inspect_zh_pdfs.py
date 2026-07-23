"""临时脚本：检查中文论文 PDF 的页数和切分情况（构建数据集前用）。"""

from __future__ import annotations

from pathlib import Path

from research_rag.chunker import ChunkerConfig, chunk_pages
from research_rag.pdf_parser import parse_pdf


def main() -> None:
    cfg = ChunkerConfig()
    pdf_dir = Path("eval/pdfs/zh")
    for p in sorted(pdf_dir.glob("*.pdf")):
        r = parse_pdf(p)
        chunks = chunk_pages(r.pages, cfg)
        print(f"{p.name}: {r.page_count} pages, {len(chunks)} chunks")
        for i, c in enumerate(chunks[:2]):
            preview = c.content[:150].replace("\n", " ")
            print(f"  chunk[{i}] p{c.start_page}-{c.end_page}: {preview}")
        print()


if __name__ == "__main__":
    main()
