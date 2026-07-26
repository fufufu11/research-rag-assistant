import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Help } from "./Help";

describe("Help", () => {
  it("渲染帮助页根元素 + 标题", () => {
    render(<Help />);
    expect(screen.getByTestId("help-page")).toBeInTheDocument();
    expect(screen.getByText("帮助")).toBeInTheDocument();
  });

  it("渲染项目简介章节", () => {
    render(<Help />);
    expect(screen.getByText("项目简介")).toBeInTheDocument();
  });

  it("渲染快速上手章节含快捷键说明", () => {
    render(<Help />);
    expect(screen.getByText("快速上手")).toBeInTheDocument();
    expect(screen.getByText("快捷键")).toBeInTheDocument();
  });

  it("渲染 API Key 与免责声明章节", () => {
    render(<Help />);
    expect(screen.getByText("API Key")).toBeInTheDocument();
    expect(screen.getByText("免责声明")).toBeInTheDocument();
  });
});
