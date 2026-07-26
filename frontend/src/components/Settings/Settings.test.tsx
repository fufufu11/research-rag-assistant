import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Settings } from "./Settings";
import { AppProvider } from "../../store/AppContext";

function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <Settings />
      </AppProvider>
    </QueryClientProvider>,
  );
}

describe("Settings", () => {
  it("渲染设置页根元素 + 标题", () => {
    renderWithProviders();
    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    expect(screen.getByText("设置")).toBeInTheDocument();
  });

  it("渲染 API Key 输入框 + 保存 + 清除按钮", () => {
    renderWithProviders();
    expect(screen.getByTestId("api-key-input")).toBeInTheDocument();
    expect(screen.getByTestId("save-api-key-btn")).toBeInTheDocument();
    expect(screen.getByTestId("clear-api-key-btn")).toBeInTheDocument();
  });

  it("localStorage 已有 API key 时初始化到输入框", () => {
    window.localStorage.setItem("rag_api_key", "existing-key");
    renderWithProviders();
    const input = screen.getByTestId("api-key-input") as HTMLInputElement;
    expect(input.value).toBe("existing-key");
    window.localStorage.clear();
  });

  it("点击保存按钮持久化 API key 到 localStorage", () => {
    renderWithProviders();
    const input = screen.getByTestId("api-key-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "new-key-123" } });
    fireEvent.click(screen.getByTestId("save-api-key-btn"));
    expect(window.localStorage.getItem("rag_api_key")).toBe("new-key-123");
    window.localStorage.clear();
  });

  it("点击清除按钮清空 input 与 localStorage", () => {
    window.localStorage.setItem("rag_api_key", "old-key");
    renderWithProviders();
    fireEvent.click(screen.getByTestId("clear-api-key-btn"));
    expect(window.localStorage.getItem("rag_api_key")).toBeNull();
    const input = screen.getByTestId("api-key-input") as HTMLInputElement;
    expect(input.value).toBe("");
  });
});
