import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { AppProvider } from "./store/AppContext";

// App 测试：需要包裹 QueryClientProvider + AppProvider
function renderWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <App />
      </AppProvider>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  it("渲染根布局容器（class=app）", () => {
    const { container } = renderWithProviders();
    const root = container.querySelector(".app");
    expect(root).not.toBeNull();
    expect(screen.getByTestId("app-root")).toBeInTheDocument();
  });

  it("默认渲染 chat 视图（含欢迎占位与左侧栏）", () => {
    renderWithProviders();
    expect(screen.getByTestId("sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("chat-area")).toBeInTheDocument();
    expect(screen.getByText("科研文献智能问答")).toBeInTheDocument();
  });
});
