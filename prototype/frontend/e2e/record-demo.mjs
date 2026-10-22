// 備份 Demo 影片：照 Demo Day 劇本用 UI 操作一遍並錄影（1600×1000）。
// 用法：先啟動前後端，然後
//   E2E_BASE_URL=http://localhost:3000 node e2e/record-demo.mjs
// 輸出：prototype/assets/demo/backup_demo.webm（有 ffmpeg 時另轉 backup_demo.mp4）
import { chromium } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, renameSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3040";
const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "../../assets/demo");
const TMP = path.join(OUT, ".raw");
mkdirSync(TMP, { recursive: true });

const size = { width: 1600, height: 1000 };
// --window-size：不加的話新版 headless Chrome 錄出來底部會有一條灰帶
const browser = await chromium.launch({ channel: "chrome", args: [`--window-size=${size.width},${size.height}`] });
const ctx = await browser.newContext({ viewport: size, recordVideo: { dir: TMP, size }, locale: "zh-TW", timezoneId: "Asia/Taipei" });
const page = await ctx.newPage();
page.on("dialog", (d) => void d.accept());
page.setDefaultTimeout(120_000);

const PACE = Number(process.env.DEMO_PACE ?? 1.8); // 放慢倍率，讓觀眾跟得上（約 4–5 分鐘）
const pause = (ms) => page.waitForTimeout(ms * PACE);
const go = async (p) => {
  await page.goto(BASE + p);
  await pause(800);
};
const role = async (r) => {
  await page.getByLabel("切換身分").selectOption(r);
  await pause(900);
};
const card = (title) =>
  page.locator("h2", { hasText: title }).first().locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]");
const scrollTo = async (loc) => {
  await loc.scrollIntoViewIfNeeded();
  await pause(600);
};
const toast = (t) => page.locator("div.pointer-events-none.fixed >> div").filter({ hasText: t }).first().waitFor();
const status = (label) => page.locator("h1").locator("..").getByText(label, { exact: true }).first().waitFor();
const scenario = async (prefix) => {
  await go("/control");
  await page.getByRole("button", { name: new RegExp(prefix) }).first().click();
  await pause(1500);
};
const openWo = async (title) => {
  await go("/kanban");
  const link = page.locator("a[href^='/workorders/']").filter({ hasText: title }).first();
  await link.waitFor({ timeout: 90_000 });
  await pause(1500);
  await link.click();
  await page.waitForURL(/\/workorders\//);
  await pause(2500);
};
const chat = async (agent, text) => {
  const input = agent.getByPlaceholder("輸入現場狀況…");
  const n = await agent.locator("div.rounded-tl-none").count();
  await input.click();
  await input.pressSequentially(text, { delay: 60 });
  await pause(400);
  await agent.getByRole("button", { name: "送出" }).click();
  await page.waitForFunction(
    ([sel, before]) => document.querySelectorAll(sel).length > before,
    [`div.rounded-tl-none`, n],
    { timeout: 120_000 },
  );
  await agent.getByText("Agent 整理工單、SOP 與訊號中…").waitFor({ state: "detached" }).catch(() => {});
  await agent.locator("span.animate-pulse.bg-cyan-400").first().waitFor({ state: "detached" }).catch(() => {});
  await pause(2500);
};

const t0 = Date.now();
const mark = (s) => console.log(`${((Date.now() - t0) / 1000).toFixed(0).padStart(4)}s  ${s}`);

// 0. 重置、總覽
await go("/control");
await role("technician");
await page.getByRole("button", { name: "重置", exact: true }).click();
await toast("Demo 已重置");
await go("/");
mark("儀表板總覽");
await pause(6000);

// 1. PPE
await scenario("場景 1");
mark("場景 1 PPE");
await go("/");
await card("即時告警").getByText(/安全帽/).first().waitFor({ timeout: 90_000 });
await pause(6000);
await openWo(/安全帽/);
await pause(4000);

// 2. 卡料
await scenario("場景 2");
mark("場景 2 卡料");
await go("/");
await card("即時告警").getByText(/卡料/).first().waitFor({ timeout: 90_000 });
await pause(7000);
await openWo(/卡料/);
await page.getByText("根因前三名").first().scrollIntoViewIfNeeded();
await pause(4000);
await page.mouse.wheel(0, -2000);

// 接單 → 許可 → 班長核准
await page.getByRole("button", { name: "接單", exact: true }).click();
await status("已接單");
await pause(1500);
const permit = card(/作業許可/);
await scrollTo(permit);
await permit.getByRole("button", { name: "申請作業許可" }).click();
await status("許可申請中");
await pause(1500);
await role("leader");
await permit.getByRole("button", { name: "核准作業許可" }).click();
await status("許可已核准");
mark("作業許可核准");
await pause(1500);
await role("technician");

// LOTO
const loto = card(/LOTO 上鎖掛牌/);
await scrollTo(loto);
for (let i = 0; i < 20; i++) {
  const b = loto.locator("ul button:enabled").first();
  if (!(await b.count())) break;
  await b.click();
  await pause(900);
}
await status("維修中");
mark("LOTO 完成 → 維修中");

// 維修 Agent
const agent = card("維修 Agent");
await scrollTo(agent);
await chat(agent, "開始排查");
await chat(agent, "滾輪看起來正常，還是卡");
await chat(agent, "卡住的料片可以直接伸手拿嗎？");
await agent.getByText(/安全把關檢查中/).first().waitFor({ state: "detached", timeout: 120_000 }).catch(() => {});
mark("安全把關");
await pause(4000);
await role("supervisor");
for (let i = 0; i < 6; i++) {
  const b = agent.getByRole("button", { name: "主管確認", exact: true });
  if (!(await b.count())) break;
  await b.first().scrollIntoViewIfNeeded();
  await pause(800);
  await b.first().click();
  await pause(1500);
}
await role("technician");

// Checklist → 結案回報
const checklist = card("完工 Checklist");
await scrollTo(checklist);
for (let i = 0; i < 12; i++) {
  const b = checklist.locator("ul button:enabled").first();
  if (!(await b.count())) break;
  await b.click();
  await pause(800);
}
await status("待確認");
const report = card("結案回報");
await scrollTo(report);
await report.getByRole("button", { name: "帶入範例" }).click();
await pause(2500);
await report.getByRole("button", { name: /提交回報/ }).click();
await status("已結案");
mark("結案");
await pause(2500);
await role("supervisor");
await report.getByRole("button", { name: "核准入庫", exact: true }).click();
await toast(/已核准入庫/);
mark("核准入庫");
await pause(3000);

// 3. 人員進入危險區 → 職災通報倒數
await scenario("場景 6");
mark("場景 6 職災通報");
await go("/");
await card("即時告警").getByText(/危險區/).first().waitFor({ timeout: 90_000 });
await pause(2500);
// 傷亡由主管在畫面上標記（系統不從影像推論傷亡）
await openWo(/危險區|光幕/);
await role("supervisor");
const rep = card(/職業災害 8 小時通報/);
await scrollTo(rep);
await rep.getByLabel(/需住院/).fill("1");
await rep.getByLabel(/罹災/).fill("1");
await pause(1500);
await rep.getByRole("button", { name: "標記傷亡", exact: true }).click();
await toast(/8 小時通報倒數開始/);
await pause(4000);
await go("/");
await page.locator("a").filter({ hasText: "職業災害 8 小時通報" }).first().waitFor({ timeout: 30_000 });
await pause(5000);

// 4. Log 失準（影像閒置但 Log 顯示運轉）
await scenario("場景 3");
mark("場景 3 Log 失準");
await go("/");
await card("即時告警").getByText(/Log|失準|閒置/).first().waitFor({ timeout: 90_000 }).catch(() => {});
await pause(6000);
await go("/kanban");
await pause(4000);

// 5. 效益
await go("/metrics");
mark("效益頁");
await pause(7000);

await ctx.close();
await browser.close();
const vid = await page.video().path();
const webm = path.join(OUT, "backup_demo.webm");
rmSync(webm, { force: true });
renameSync(vid, webm);
rmSync(TMP, { recursive: true, force: true });
mark(`錄影完成 → ${webm}`);
try {
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", path.join(OUT, "backup_demo.mp4")]);
  console.log("已轉 mp4");
} catch {
  if (!existsSync(path.join(OUT, "backup_demo.mp4"))) console.log("沒有 ffmpeg，只留 webm");
}
