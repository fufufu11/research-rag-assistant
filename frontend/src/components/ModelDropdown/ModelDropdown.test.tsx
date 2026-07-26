import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ModelDropdown } from "./ModelDropdown";

describe("ModelDropdown", () => {
  it("渲染容器与 dot 装饰", () => {
    const { container } = render(<ModelDropdown />);
    expect(screen.getByTestId("model-dropdown")).toBeInTheDocument();
    const dot = container.querySelector(".model-dropdown .dot");
    expect(dot).not.toBeNull();
  });

  it("渲染 research-rag 占位 badge", () => {
    render(<ModelDropdown />);
    expect(screen.getByText("占位")).toBeInTheDocument();
  });

  it("select 元素被 disabled，不响应切换", () => {
    render(<ModelDropdown />);
    const select = document.querySelector(
      ".model-dropdown select",
    ) as HTMLSelectElement | null;
    expect(select).not.toBeNull();
    expect(select?.disabled).toBe(true);
  });

  it("select 默认值为 research-rag", () => {
    render(<ModelDropdown />);
    const select = document.querySelector(
      ".model-dropdown select",
    ) as HTMLSelectElement | null;
    expect(select?.value).toBe("research-rag");
  });
});
