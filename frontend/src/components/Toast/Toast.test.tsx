import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Toast } from "./Toast";

describe("Toast", () => {
  it("message 为 null 时不渲染", () => {
    const { container } = render(
      <Toast message={null} onClose={vi.fn()} />,
    );
    expect(container.querySelector(".toast")).toBeNull();
  });

  it("有 message 时渲染 .toast 元素", () => {
    render(<Toast message="已复制" onClose={vi.fn()} />);
    expect(screen.getByText("已复制")).toBeInTheDocument();
  });

  it("duration 后调用 onClose", async () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message="提示" onClose={onClose} duration={1000} />);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(onClose).toHaveBeenCalled();
    vi.useRealTimers();
  });
});
