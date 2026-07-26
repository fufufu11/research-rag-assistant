import type { CitationRead } from "../../api/types";

// CitationCard：引用卡片，渲染单个引用来源
// - 4 色循环彩色左边框（cite-1 ~ cite-4）
// - 显示文档名 + 页码范围 + snippet 摘要 + score
// - 设计稿：.trae/handoffs/ui_claude_v1.html .cite-card
interface CitationCardProps {
  citation: CitationRead;
  index: number; // 从 1 开始
}

const CITE_COLOR_COUNT = 4;

export function CitationCard({ citation, index }: CitationCardProps) {
  const colorClass = `cite-${((index - 1) % CITE_COLOR_COUNT) + 1}`;
  const pageRange =
    citation.start_page === citation.end_page
      ? `第 ${citation.start_page} 页`
      : `第 ${citation.start_page}-${citation.end_page} 页`;

  const scorePercent = Math.round((citation.score ?? 0) * 100);

  return (
    <div
      className={`cite-card ${colorClass}`}
      data-testid={`cite-card-${index}`}
    >
      <span className="num">{index}</span>
      <div className="info">
        <div className="doc-name" title={citation.document_name}>
          {citation.document_name}
        </div>
        <div className="doc-meta">
          {pageRange} · chunk #{citation.chunk_index}
        </div>
        {citation.snippet && (
          <div className="snippet">{citation.snippet}</div>
        )}
        <div className="score">score {scorePercent}%</div>
      </div>
    </div>
  );
}
