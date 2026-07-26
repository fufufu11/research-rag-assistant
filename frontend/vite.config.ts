/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// T1 阶段：最小骨架配置。
// - dev server 5173，API 请求代理到后端 8000，避免开发环境 CORS 问题
// - test 环境用 jsdom，globals: true 让 vitest 直接用 describe/it/expect
// 参考 ADR 0005：后端托管静态文件为生产方案，dev 用代理即可。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
