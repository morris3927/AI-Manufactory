import { expect, type Locator, type Page } from "@playwright/test";

export type RoleValue = "technician" | "leader" | "supervisor" | "judge";

/** 收集 console error / pageerror，測試結束時檢查 */
export function watchConsole(page: Page) {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    // 背景輪詢在換頁時被中斷、WebSocket 重連，不算 UI 錯誤
    if (/Failed to fetch|net::ERR_ABORTED|WebSocket|AbortError/i.test(t)) return;
    errors.push(t);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("dialog", (d) => void d.accept());
  return errors;
}

/** 右上角身分切換（真的去選 <select>，不改 localStorage） */
export async function switchRole(page: Page, role: RoleValue) {
  const sel = page.getByLabel("切換身分");
  await sel.selectOption(role);
  await expect(sel).toHaveValue(role);
}

export async function expectToast(page: Page, text: string | RegExp, timeout = 20_000) {
  await expect(page.locator("div.pointer-events-none.fixed >> div").filter({ hasText: text }).first()).toBeVisible({ timeout });
}

/** 控制台按「重置」→ 確認對話框 → 看到 toast */
export async function resetDemo(page: Page) {
  await page.goto("/control");
  await switchRole(page, "technician");
  await page.getByRole("button", { name: "重置", exact: true }).click();
  await expectToast(page, "Demo 已重置");
}

/** 控制台按劇本按鈕（以標題前綴辨識，例如「場景 2」） */
export async function runScenario(page: Page, titlePrefix: string) {
  await page.goto("/control");
  const btn = page.getByRole("button", { name: new RegExp(titlePrefix) }).first();
  await expect(btn).toBeEnabled();
  await btn.click();
  await expectToast(page, /劇本已啟動|RU-6 去重/);
}

/** 卡片：用 CardHeader 的 <h2> 標題找到所在的 Card */
export function card(page: Page, title: string | RegExp): Locator {
  return page
    .locator("h2", { hasText: title })
    .first()
    .locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]");
}

/** 工單頁標頭的狀態 Badge */
export async function expectStatus(page: Page, label: string, timeout = 30_000) {
  await expect(page.locator("h1").locator("..").getByText(label, { exact: true }).first()).toBeVisible({ timeout });
}

/** 從任務看板「待處理」欄點進符合標題的工單，回傳工單 ID */
export async function openFromKanban(page: Page, title: RegExp): Promise<string> {
  await page.goto("/kanban");
  const col = page.locator("div").filter({ has: page.getByText("待處理", { exact: true }) }).filter({ has: page.locator("a[href^='/workorders/']") }).last();
  const link = col.locator("a[href^='/workorders/']").filter({ hasText: title }).first();
  await expect(link).toBeVisible({ timeout: 60_000 });
  await link.click();
  await page.waitForURL(/\/workorders\/[^/]+$/);
  const id = decodeURIComponent(page.url().split("/").pop()!);
  await expect(page.locator("h1")).toBeVisible();
  return id;
}

/** 依序點 LOTO 步驟直到全部完成（每次只有下一步可點） */
export async function tickAllLoto(page: Page, scope: Locator, pause = 0) {
  const header = scope.getByText(/^\d+\/\d+$/).first();
  for (let i = 0; i < 20; i++) {
    const t = (await header.textContent()) ?? "0/1";
    const [a, b] = t.split("/").map(Number);
    if (a === b) return;
    const next = scope.locator("ul button:enabled").first();
    await expect(next).toBeVisible();
    await next.click();
    // 手機版勾完最後一步會自動跳到 Agent 分頁（LOTO 卡片卸載），也算完成
    if (a + 1 === b) {
      await expect.poll(async () => ((await header.count()) ? await header.textContent() : `${b}/${b}`), { timeout: 15_000 }).toBe(`${b}/${b}`);
      return;
    }
    await expect(header).toHaveText(`${a + 1}/${b}`, { timeout: 15_000 });
    if (pause) await page.waitForTimeout(pause);
  }
  throw new Error("LOTO 沒有在 20 步內完成");
}

/** 送一則訊息給維修 Agent，等到回覆完成（輸入框重新可用、串流游標消失、回覆數 +1） */
export async function chat(page: Page, scope: Locator, text: string) {
  const input = scope.getByPlaceholder("輸入現場狀況…");
  const assistant = scope.locator("div.rounded-tl-none");
  const before = await assistant.count();
  await input.fill(text);
  await scope.getByRole("button", { name: "送出" }).click();
  await expect(scope.getByText(text, { exact: true }).last()).toBeVisible();
  await expect.poll(async () => assistant.count(), { timeout: 120_000 }).toBeGreaterThan(before);
  // 等串流與「整理中」都結束
  await expect(scope.getByText("Agent 整理工單、SOP 與訊號中…")).toHaveCount(0, { timeout: 120_000 });
  await expect(scope.locator("span.animate-pulse.bg-cyan-400")).toHaveCount(0, { timeout: 120_000 });
  await expect(scope.getByRole("button", { name: "送出" })).toBeDisabled(); // 空字串時應 disabled
}

/** 等 Jev 安全把關跑完 */
export async function waitGate(scope: Locator) {
  await expect(scope.getByText(/安全把關檢查中/)).toHaveCount(0, { timeout: 120_000 });
}
