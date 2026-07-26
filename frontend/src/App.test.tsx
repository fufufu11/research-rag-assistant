import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

describe("App", () => {
  it("渲染 hello world 标题（T1 骨架占位）", () => {
    render(<App />);
    expect(
      screen.getByText(/科研文献智能问答/),
    ).toBeInTheDocument();
  });

  it("渲染 API 健康检查占位区块", () => {
    render(<App />);
    expect(screen.getByTestId("api-health-placeholder")).toBeInTheDocument();
  });
});
