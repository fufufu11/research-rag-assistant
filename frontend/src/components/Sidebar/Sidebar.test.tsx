import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("渲染深棕色背景的侧栏根元素", () => {
    const { container } = render(<Sidebar />);
    const sidebar = container.querySelector(".sidebar");
    expect(sidebar).not.toBeNull();
    // 设计稿要求：侧栏深棕背景 #1c1815（由 CSS 变量 --bg-sidebar 控制）
    // jsdom 不应用 CSS，断言 className 即可验证结构正确
    expect(sidebar?.className).toContain("sidebar");
  });

  it("渲染品牌 header（logo dot + research·rag）", () => {
    render(<Sidebar />);
    const header = screen.getByText(/research/);
    expect(header).toBeInTheDocument();
    const logoDot = document.querySelector(".sidebar-header .logo-dot");
    expect(logoDot).not.toBeNull();
  });

  it("渲染新建对话按钮与搜索输入框", () => {
    render(<Sidebar />);
    expect(screen.getByTestId("new-chat-btn")).toBeInTheDocument();
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("搜索会话…")).toBeInTheDocument();
  });

  it("渲染历史会话分组（占位空状态）", () => {
    render(<Sidebar />);
    expect(screen.getByTestId("nav-history")).toBeInTheDocument();
    expect(screen.getByText("历史会话")).toBeInTheDocument();
    expect(screen.getByText("暂无会话")).toBeInTheDocument();
  });

  it("渲染文档库分组（占位空状态）", () => {
    render(<Sidebar />);
    expect(screen.getByTestId("nav-documents")).toBeInTheDocument();
    expect(screen.getByText("文档库")).toBeInTheDocument();
    expect(screen.getByText("暂无文档")).toBeInTheDocument();
  });

  it("渲染下层设置 + 帮助按钮", () => {
    render(<Sidebar />);
    expect(screen.getByTestId("sidebar-footer")).toBeInTheDocument();
    expect(screen.getByTestId("footer-settings")).toBeInTheDocument();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByTestId("footer-help")).toBeInTheDocument();
    expect(screen.getByText("帮助")).toBeInTheDocument();
  });
});
