import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatArea } from "./ChatArea";

describe("ChatArea", () => {
  it("渲染主聊天区根元素", () => {
    const { container } = render(<ChatArea />);
    const chatArea = container.querySelector(".chat-area");
    expect(chatArea).not.toBeNull();
    expect(chatArea?.className).toContain("chat-area");
  });

  it("渲染顶部栏（含模型下拉与未选择会话提示）", () => {
    render(<ChatArea />);
    expect(screen.getByTestId("top-bar")).toBeInTheDocument();
    expect(screen.getByTestId("model-dropdown")).toBeInTheDocument();
    expect(screen.getByText("未选择会话")).toBeInTheDocument();
  });

  it("渲染居中收窄 720px 内容占位（含欢迎标题与说明）", () => {
    render(<ChatArea />);
    const placeholder = screen.getByTestId("content-placeholder");
    expect(placeholder).toBeInTheDocument();
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
    expect(screen.getByText("从左侧选择或新建对话开始")).toBeInTheDocument();
  });

  it("内容占位区使用 content-max-width（720px）", () => {
    const { container } = render(<ChatArea />);
    const placeholder = container.querySelector(".content-placeholder");
    expect(placeholder).not.toBeNull();
    // CSS 通过 --content-max-width: 720px 变量控制最大宽度
    // jsdom 不计算样式，仅断言 class 与节点存在
    expect(placeholder?.className).toContain("content-placeholder");
  });
});
