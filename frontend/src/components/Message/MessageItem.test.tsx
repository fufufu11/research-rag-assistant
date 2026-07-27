import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MessageRead } from "../../api/types";
import { MessageItem } from "./MessageItem";

const citedAnswer: MessageRead = {
  id: "message-1",
  role: "assistant",
  content: "依据 **实验结果**，该方法更准确 [C3]。",
  citations: [
    {
      document_id: "document-1",
      document_name: "research-paper.pdf",
      start_page: 7,
      end_page: 9,
      chunk_index: 42,
      snippet:
        "完整证据片段说明该方法在三个数据集上获得了更高准确率，并给出了消融实验。",
      score: 0.873,
    },
  ],
  request_id: "request-1",
  created_at: "2026-07-27T00:00:00Z",
};

describe("MessageItem", () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("keeps the actual citation marker and exposes its evidence without internal scores", () => {
    render(<MessageItem message={citedAnswer} />);

    expect(screen.getByText("实验结果").tagName).toBe("STRONG");
    expect(
      screen.getByRole("button", { name: "查看引用 C3" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("citation-C3")).toHaveTextContent(
      "research-paper.pdf",
    );
    expect(screen.getByTestId("citation-C3")).toHaveTextContent("第 7-9 页");
    expect(screen.queryByText(/42|87%|score/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开引用 C3" }));
    expect(screen.getByTestId("citation-C3")).toHaveTextContent(
      "完整证据片段说明该方法在三个数据集上获得了更高准确率，并给出了消融实验。",
    );
  });

  it("copies the plain answer with a concise source list", async () => {
    const onCopied = vi.fn();
    render(<MessageItem message={citedAnswer} onCopied={onCopied} />);

    fireEvent.click(screen.getByRole("button", { name: "复制回答" }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledOnce();
    });
    const copied = vi.mocked(navigator.clipboard.writeText).mock.calls[0][0];
    expect(copied).toContain("依据 实验结果，该方法更准确 [C3]。");
    expect(copied).toContain("来源：");
    expect(copied).toContain("[C3] research-paper.pdf，第 7-9 页");
    expect(copied).not.toMatch(/完整证据片段|87%|chunk|\*\*/i);
    expect(onCopied).toHaveBeenCalledOnce();
  });

  it("moves focus to and highlights the matching citation card", () => {
    render(<MessageItem message={citedAnswer} />);
    const card = screen.getByTestId("citation-C3");
    const scrollIntoView = vi.fn();
    card.scrollIntoView = scrollIntoView;

    fireEvent.click(screen.getByRole("button", { name: "查看引用 C3" }));

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "nearest",
    });
    expect(card).toHaveFocus();
    expect(card).toHaveClass("citation-highlight");
  });

  it("targets the citation card in the same turn when labels repeat", () => {
    render(
      <>
        <MessageItem message={citedAnswer} />
        <MessageItem message={{ ...citedAnswer, id: "message-2" }} />
      </>,
    );
    const markers = screen.getAllByRole("button", { name: "查看引用 C3" });
    const cards = screen.getAllByTestId("citation-C3");

    fireEvent.click(markers[1]);

    expect(cards[1]).toHaveFocus();
    expect(cards[1]).toHaveClass("citation-highlight");
    expect(cards[0]).not.toHaveClass("citation-highlight");
  });

  it("shows generation state without completed-answer actions while streaming", () => {
    render(<MessageItem message={{ ...citedAnswer, content: "partial" }} isStreaming />);

    expect(screen.getByLabelText("生成中")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制回答" })).toBeNull();
  });
});
