import type { CitationRead } from "../../api/types";

export function formatCitationPages(citation: CitationRead): string {
  return citation.start_page === citation.end_page
    ? `第 ${citation.start_page} 页`
    : `第 ${citation.start_page}-${citation.end_page} 页`;
}
