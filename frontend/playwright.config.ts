import { defineConfig, devices } from "@playwright/test";

/**
 * 前端浏览器回归测试配置。
 *
 * E2E 仅校验浏览器中的用户交互、接口调用和 DTO 展示映射；它通过 page.route
 * 模拟 API，避免本地执行测试时写入开发数据库。真实 API、数据库和 Worker
 * 恢复语义由 backend/tests 中的集成测试覆盖。
 */
const port = Number(process.env.PLAYWRIGHT_PORT ?? "4173");
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  use: { baseURL, trace: "on-first-retry", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
