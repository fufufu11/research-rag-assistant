import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CitationCard } from "./CitationCard";
import type { CitationRead } from "../../api/types";

const baseCitation: CitationRead = {
  document_id: "doc-1",
  document_name: "paper1.pdf",
  start_page: 3,
  end_page: 5,
  chunk_index: 2,
  snippet: "这是检索到的片段内容",
  score: 0.92,
};

describe("CitationCard", () => {
  it("渲染文档名 + 页码范围 + chunk + snippet + score", () => {
    render(<CitationCard citation={baseCitation} index={1} />);
    expect(screen.getByText("paper1.pdf")).toBeInTheDocument();
    expect(screen.getByText(/第 3-5 页/)).toBeInTheDocument();
    expect(screen.getByText(/chunk #2/)).toBeInTheDocument();
    expect(screen.getByText("这是检索到的片段内容")).toBeInTheDocument();
    expect(screen.getByText("score 92%")).toBeInTheDocument();
  });

  it("start_page === end_page 时显示单页", () => {
    render(
      <CitationCard
        citation={{ ...baseCitation, start_page: 7, end_page: 7 }}
        index={1}
      />,
    );
    expect(screen.getByText(/第 7 页/)).toBeInTheDocument();
  });

  it("4 色循环：index 1-4 对应 cite-1 到 cite-4，index 5 回到 cite-1", () => {
    const { container, rerender } = render(
      <CitationCard citation={baseCitation} index={1} />,
    );
    expect(container.querySelector(".cite-card.cite-1")).not.toBeNull();

    rerender(<CitationCard citation={baseCitation} index={2} />);
    expect(container.querySelector(".cite-card.cite-2")).not.toBeNull();

    rerender(<CitationCard citation={baseCitation} index={3} />);
    expect(container.querySelector(".cite-card.cite-3")).not.toBeNull();

    rerender(<CitationCard citation={baseCitation} index={4} />);
    expect(container.querySelector(".cite-card.cite-4")).not.toBeNull();

    rerender(<CitationCard citation={baseCitation} index={5} />);
    expect(container.querySelector(".cite-card.cite-1")).not.toBeNull();
  });

  it("snippet 为空时不渲染 snippet 块", () => {
    const { container } = render(
      <CitationCard
        citation={{ ...baseCitation, snippet: "" }}
        index={1}
      />,
    );
    expect(container.querySelector(".snippet")).toBeNull();
  });
});
