import { defineConfig } from "@playwright/test";

// 端到端點擊測試：只透過瀏覽器 UI 操作（身分切換、按鈕、表單、對話框），不直接打 API 改狀態。
// 需要先啟動後端與前端 production build，見 README「端到端測試」。
export default defineConfig({
  testDir: "./e2e",
  timeout: 8 * 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1, // 同一個後端、同一份 Demo 資料，測試要依序跑
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3040",
    channel: "chrome", // 用本機 Chrome，不另外下載瀏覽器
    viewport: { width: 1600, height: 1000 },
    locale: "zh-TW",
    timezoneId: "Asia/Taipei",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
