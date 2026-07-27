import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// 直接读 CSS 文件原文断言设计 token 完整性
// （vitest css: false 配置下 ?raw 后缀返回空字符串，故用 fs.readFileSync）
const __dirname = dirname(fileURLToPath(import.meta.url));
const claudeTheme = readFileSync(
  join(__dirname, "claude-theme.css"),
  "utf-8",
);
const globals = readFileSync(join(__dirname, "globals.css"), "utf-8");

describe("claude-theme.css 设计 token 完整性", () => {
  it("包含背景与表面色变量", () => {
    const expectedBgVars = [
      "--bg-canvas: #faf9f7",
      "--bg-surface: #ffffff",
      "--bg-sidebar: #1c1815",
      "--bg-sidebar-soft: #28221e",
      "--bg-input: #ffffff",
      "--bg-code: #f4efe8",
    ];
    for (const v of expectedBgVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("包含文字色变量", () => {
    const expectedFgVars = [
      "--fg-primary: #2d2a26",
      "--fg-secondary: #6b655c",
      "--fg-muted: #9b948a",
      "--fg-on-dark: #f5efe6",
      "--fg-on-dark-muted: #a8a094",
    ];
    for (const v of expectedFgVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("包含强调色（赤陶土）变量", () => {
    const expectedAccentVars = [
      "--accent: #c96442",
      "--accent-soft: #e8a48a",
      "--accent-bg: #fdf3ee",
      "--accent-hover: #b75636",
    ];
    for (const v of expectedAccentVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("包含边框与引用卡片彩色边框变量", () => {
    const expectedVars = [
      "--border: #ebe5dc",
      "--border-strong: #d9d2c5",
      "--border-on-dark: #3a322a",
      "--cite-1: #c96442",
      "--cite-2: #4a7c59",
      "--cite-3: #8b5a8c",
      "--cite-4: #2c5f8a",
    ];
    for (const v of expectedVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("包含阴影与布局尺寸变量", () => {
    const expectedVars = [
      "--shadow-sm:",
      "--shadow-md:",
      "--shadow-lg:",
      "--shadow-input:",
      "--sidebar-width: 260px",
      "--content-max-width: 720px",
    ];
    for (const v of expectedVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("包含交互与状态语义变量", () => {
    const expectedVars = [
      "--focus-ring:",
      "--bg-top-bar:",
      "--bg-selected-on-dark:",
      "--bg-error-on-dark:",
      "--bg-error-on-dark-soft:",
      "--border-error-on-dark:",
      "--fg-error-on-dark:",
      "--fg-success-on-dark:",
      "--fg-warning-on-dark:",
      "--fg-success:",
      "--fg-error:",
    ];
    for (const v of expectedVars) {
      expect(claudeTheme).toContain(v);
    }
  });

  it("globals.css 导入 claude-theme.css 并定义根布局 .app 网格", () => {
    expect(globals).toContain('@import "./claude-theme.css";');
    expect(globals).toContain(
      "grid-template-columns: var(--sidebar-width) 1fr",
    );
    expect(globals).toContain("height: 100vh");
  });

  it("globals.css 包含左侧栏深棕背景与右侧主区暖米色背景", () => {
    expect(globals).toContain("background: var(--bg-sidebar)");
    expect(globals).toContain("background: var(--bg-canvas)");
  });

  it("globals.css 包含模型下拉占位样式", () => {
    expect(globals).toContain(".model-dropdown");
    expect(globals).toContain("cursor: not-allowed");
  });

  it("globals.css 包含居中收窄 720px 内容区样式", () => {
    expect(globals).toContain(".content-placeholder");
    expect(globals).toContain("max-width: var(--content-max-width)");
  });
});
