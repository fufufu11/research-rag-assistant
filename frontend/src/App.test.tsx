import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

describe("App", () => {
  it("渲染根布局容器（class=app）", () => {
    const { container } = render(<App />);
    const root = container.querySelector(".app");
    expect(root).not.toBeNull();
    expect(screen.getByTestId("app-root")).toBeInTheDocument();
  });

  it("同时渲染左侧栏与右侧主聊天区", () => {
    render(<App />);
    expect(screen.getByTestId("sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("chat-area")).toBeInTheDocument();
  });

  it("渲染品牌标题（科研文献智能问答）于内容占位区", () => {
    render(<App />);
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
  });
});
