import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { CitationRead } from "../../api/types";
import { formatCitationPages } from "./citation";

interface CitationCardProps {
  citation: CitationRead;
  label: string;
  targetId: string;
}

export function CitationCard({ citation, label, targetId }: CitationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const pageLabel = formatCitationPages(citation);

  return (
    <article
      className="citation-card"
      id={targetId}
      data-testid={`citation-${label}`}
      tabIndex={-1}
    >
      <div className="citation-heading">
        <span className="citation-label">{label}</span>
        <div className="citation-source">
          <strong title={citation.document_name}>{citation.document_name}</strong>
          <span>{pageLabel}</span>
        </div>
      </div>
      <p className={`citation-snippet ${expanded ? "expanded" : ""}`}>
        {citation.snippet}
      </p>
      <button
        type="button"
        className="citation-expand"
        aria-label={`${expanded ? "收起" : "展开"}引用 ${label}`}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? (
          <ChevronUp aria-hidden="true" size={14} />
        ) : (
          <ChevronDown aria-hidden="true" size={14} />
        )}
        {expanded ? "收起" : "展开证据"}
      </button>
    </article>
  );
}
