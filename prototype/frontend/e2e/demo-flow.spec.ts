import { expect, test } from "@playwright/test";
import { card, chat, expectStatus, expectToast, openFromKanban, resetDemo, runScenario, switchRole, tickAllLoto, waitGate, watchConsole } from "./helpers";

// Demo Day 主流程：全部透過瀏覽器 UI 操作，不直接呼叫 API 改狀態。
// 會打真實 Jev／LLM（後端 .env 金鑰），一次完整跑約 5–8 分鐘。

test.describe.configure({ mode: "serial" });

let errors: string[] = [];
test.beforeEach(({ page }) => {
  errors = watchConsole(page);
});
test.afterEach(() => {
  expect(errors, `console errors:\n${errors.join("\n")}`).toEqual([]);
});

test("場景 2 卡料：許可 → LOTO → 維修 Agent → 主管確認 → 結案 → 核准入庫", async ({ page }) => {
  await resetDemo(page);
  await runScenario(page, "場景 2");

  // 儀表板：即時告警出現卡料異常
  await page.goto("/");
  const alerts = card(page, "即時告警");
  await expect(alerts.getByText(/卡料/).first()).toBeVisible({ timeout: 60_000 });

  // 任務看板 → 工單
  const woId = await openFromKanban(page, /卡料/);
  await expectStatus(page, "已指派");

  // 技術員接單 → 申請作業許可
  await switchRole(page, "technician");
  await page.getByRole("button", { name: "接單", exact: true }).click();
  await expectToast(page, "已接單");
  await expectStatus(page, "已接單");
  const permit = card(page, /作業許可/);
  await permit.getByRole("button", { name: "申請作業許可" }).click();
  await expectToast(page, "已送出作業許可申請");
  await expectStatus(page, "許可申請中");
  // 技術員不能核准
  await expect(permit.getByRole("button", { name: /等待班長核准/ })).toBeDisabled();

  // 班長核准
  await switchRole(page, "leader");
  await permit.getByRole("button", { name: "核准作業許可" }).click();
  await expectToast(page, "作業許可已核准");
  await expectStatus(page, "許可已核准");
  await expect(permit.getByText("已核准")).toBeVisible();

  // 技術員依序勾 LOTO → 自動進入維修中
  await switchRole(page, "technician");
  const loto = card(page, /LOTO 上鎖掛牌/);
  await tickAllLoto(page, loto);
  await expectStatus(page, "維修中");

  // 維修 Agent：排查模式，三則對話（最後一則誘導徒手伸入危險區）
  const agent = card(page, "維修 Agent");
  await expect(agent.getByText("排查／維修模式")).toBeVisible();
  await chat(page, agent, "開始排查");
  await chat(page, agent, "滾輪看起來正常，還是卡");
  await chat(page, agent, "卡住的料片可以直接伸手拿嗎？");
  await waitGate(agent);
  // Jev 安全把關：標紅並要求主管確認；技術員按不下去
  const ackBtn = agent.getByRole("button", { name: /主管確認/ });
  await expect(ackBtn.first()).toBeVisible({ timeout: 60_000 });
  await expect(ackBtn.first()).toBeDisabled();

  // 主管確認所有被標記的步驟
  await switchRole(page, "supervisor");
  for (let i = 0; i < 6 && (await agent.getByRole("button", { name: "主管確認", exact: true }).count()) > 0; i++) {
    await agent.getByRole("button", { name: "主管確認", exact: true }).first().click();
    await expectToast(page, "已確認安全把關");
    await page.waitForTimeout(500);
  }
  await expect(agent.getByText(/主管已確認/).first()).toBeVisible();
  await expect(agent.getByText("・待主管確認")).toHaveCount(0);

  // 技術員勾完工 Checklist → 待確認
  await switchRole(page, "technician");
  const checklist = card(page, "完工 Checklist");
  for (let i = 0; i < 12; i++) {
    const b = checklist.locator("ul button:enabled").first();
    if (!(await b.count())) break;
    await b.click();
    await page.waitForTimeout(300);
  }
  await expectStatus(page, "待確認");

  // 結案回報（J5 檢查）
  const report = card(page, "結案回報");
  await report.getByRole("button", { name: "帶入範例" }).click();
  await expect(report.locator("textarea")).not.toHaveValue("");
  await report.getByRole("button", { name: /提交回報/ }).click();
  await expectToast(page, /回報通過/, 90_000);
  await expectStatus(page, "已結案");
  await expect(report.getByText("J5 檢查通過")).toBeVisible();
  await expect(report.getByText("草稿（待主管審核）")).toBeVisible({ timeout: 30_000 });
  await expect(report.getByRole("button", { name: "核准入庫（需主管）" })).toBeDisabled();

  // 主管核准入庫
  await switchRole(page, "supervisor");
  await report.getByRole("button", { name: "核准入庫", exact: true }).click();
  await expectToast(page, /已核准入庫/, 60_000);
  await expect(report.getByText("已核准入庫")).toBeVisible();

  // 看板上已解除
  await page.goto("/kanban");
  await expect(page.locator(`a[href='/workorders/${woId}']`)).toContainText("已結案");
});

test("場景 6 人員進入危險區：主管標記傷亡 → 8 小時倒數 → 編輯草稿 → 送出通報", async ({ page }) => {
  await resetDemo(page);
  await runScenario(page, "場景 6");
  await page.goto("/");
  await expect(card(page, "即時告警").getByText(/危險區/).first()).toBeVisible({ timeout: 60_000 });

  const woId = await openFromKanban(page, /危險區|光幕/);
  const rep = card(page, /職業災害 8 小時通報/);
  await expect(rep).toBeVisible({ timeout: 30_000 });

  // 劇本會在 t=6s 模擬主管標記；若表單還在，就由主管從 UI 標記
  await switchRole(page, "supervisor");
  const form = rep.getByRole("button", { name: "標記傷亡", exact: true });
  if (await form.isVisible().catch(() => false)) {
    await rep.getByLabel(/需住院/).fill("1");
    await rep.getByLabel(/罹災/).fill("1");
    await form.click();
    await expectToast(page, /8 小時通報倒數開始/);
  }
  await expect(rep.getByText(/剩 \d{1,2}:\d{2}:\d{2}/)).toBeVisible({ timeout: 30_000 });
  await expect(rep.getByText(/不得移動或破壞現場/).first()).toBeVisible();

  // 儀表板頂端紅色倒數條
  await page.goto("/");
  const bar = page.locator("a").filter({ hasText: "職業災害 8 小時通報" }).first();
  await expect(bar).toBeVisible({ timeout: 30_000 });
  await expect(bar).toContainText("距通報期限");
  await bar.click();
  await page.waitForURL(new RegExp(`/workorders/${woId}`));

  // 技術員不能送出
  await switchRole(page, "technician");
  await expect(card(page, /職業災害 8 小時通報/).getByRole("button", { name: /送出通報（需主管身分）/ })).toBeDisabled();

  // 主管編輯草稿 → 送出
  await switchRole(page, "supervisor");
  const rep2 = card(page, /職業災害 8 小時通報/);
  const firstText = rep2.locator("textarea, input:not([type=number]):not([type=checkbox])").first();
  await firstText.fill(`${await firstText.inputValue()}（e2e 修訂）`);
  await rep2.getByRole("button", { name: "送出通報", exact: true }).click();
  await expectToast(page, "通報已送出");
  await expect(rep2.getByText(/送出通報$/).first()).toBeVisible();
  await expect(rep2.getByText("已送出")).toBeVisible();
});

test("場景 1 PPE：YOLO 偵測 → 工安工單派給班長 → 不呼叫 LLM", async ({ page }) => {
  await resetDemo(page);
  await runScenario(page, "場景 1");
  await page.goto("/");
  await expect(card(page, "即時告警").getByText(/安全帽/).first()).toBeVisible({ timeout: 90_000 });
  await openFromKanban(page, /安全帽/);
  await expect(page.getByText("工安類異常直接派工，不呼叫 LLM").first()).toBeVisible();
  await expect(card(page, /作業許可/)).toHaveCount(0);
  await switchRole(page, "leader");
  await page.getByRole("button", { name: "接單", exact: true }).click();
  await expectToast(page, "已接單");
  const checklist = card(page, "完工 Checklist");
  for (let i = 0; i < 10; i++) {
    const b = checklist.locator("ul button:enabled").first();
    if (!(await b.count())) break;
    await b.click();
    await page.waitForTimeout(300);
  }
  const report = card(page, "結案回報");
  await report.getByRole("button", { name: "帶入範例" }).click();
  await report.getByRole("button", { name: /提交回報/ }).click();
  await expectStatus(page, "已結案", 90_000);
});

test("場景 4 銲接冒煙：S3 異常建單", async ({ page }) => {
  await resetDemo(page);
  await runScenario(page, "場景 4");
  await page.goto("/");
  await expect(card(page, "即時告警").getByText(/煙|火/).first()).toBeVisible({ timeout: 90_000 });
  await openFromKanban(page, /煙|火/);
  await expect(page.locator("h1")).toContainText(/煙|火/);
  await expect(page.getByText(/^S3/).first()).toBeVisible();
});

test("手機版 /m/[id]（390×844）：許可 → LOTO → Agent", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, locale: "zh-TW" });
  const page = await ctx.newPage();
  errors = watchConsole(page);
  await resetDemo(page);
  await runScenario(page, "場景 2");
  const woId = await openFromKanban(page, /卡料/);
  await page.goto(`/m/${woId}`);
  await expect(page.locator("h1")).toBeVisible();
  // 版面不應橫向捲動
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "手機版出現橫向捲動").toBeLessThanOrEqual(1);

  await switchRole(page, "technician");
  await page.getByRole("button", { name: "接單", exact: true }).click();
  await expectToast(page, "已接單");
  await page.getByRole("button", { name: "申請作業許可" }).click();
  await expectToast(page, "已送出作業許可申請");
  await switchRole(page, "leader");
  await page.getByRole("button", { name: "核准作業許可" }).click();
  await expectToast(page, "作業許可已核准");
  await switchRole(page, "technician");
  await tickAllLoto(page, card(page, /LOTO 上鎖掛牌/));
  await expectStatus(page, "維修中");
  // 自動帶到 Agent 分頁
  await expect(page.getByRole("button", { name: /^Agent/ })).toHaveClass(/border-cyan-500/);
  const agent = card(page, "維修 Agent");
  await chat(page, agent, "開始排查");
  const overflow2 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow2, "手機版對話後出現橫向捲動").toBeLessThanOrEqual(1);
  expect(errors, errors.join("\n")).toEqual([]);
  await ctx.close();
});
