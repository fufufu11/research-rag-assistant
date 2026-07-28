import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("mobile navigation opens and closes after selecting a view", () => {
    renderWithProviders();
    const menuButton = screen.getByRole("button", { name: "打开导航" });
    const sidebar = screen.getByTestId("sidebar");

    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(menuButton);
    expect(menuButton).toHaveAttribute("aria-expanded", "true");
    expect(sidebar).toHaveClass("mobile-open");

    fireEvent.click(screen.getByTestId("footer-settings"));
    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    expect(sidebar).not.toHaveClass("mobile-open");
  });
});
