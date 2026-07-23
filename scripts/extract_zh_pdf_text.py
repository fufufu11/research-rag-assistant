"""临时脚本：提取中文论文 PDF 每页文本，用于设计评测数据集。"""

from __future__ import annotations

from pathlib import Path

from research_rag.pdf_parser import parse_pdf


def main() -> None:
    pdf_dir = Path("eval/pdfs/zh")
    out_dir = Path("eval/extracted_zh")
    out_dir.mkdir(exist_ok=True)
    for p in sorted(pdf_dir.glob("*.pdf")):
        r = parse_pdf(p)
        out_file = out_dir / (p.stem + ".txt")
        lines = []
        for i, page in enumerate(r.pages, start=1):
            lines.append(f"===== PAGE {i} =====")
            lines.append(page.text)
            lines.append("")
        out_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"{p.name}: {r.page_count} pages -> {out_file}")


if __name__ == "__main__":
    main()
