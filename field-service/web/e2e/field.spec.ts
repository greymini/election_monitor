/** Browser tests on Edge against `python -m scripts.demo` (fresh sample data each run). */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { headerProblems, PAGES, signIn } from "./helpers";

test("filter the ledger and open an entry", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.goto("/field/ledger");
  await expect(page.getByTestId("ledger-row").first()).toBeVisible();
  await page.getByTestId("filter-kind").selectOption("advance");
  const rows = page.getByTestId("ledger-row");
  await expect(rows.first()).toHaveAttribute("data-kind", "advance");
  const kinds = await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-kind")));
  expect(new Set(kinds)).toEqual(new Set(["advance"]));
  await rows.first().click();
  const drawer = page.getByTestId("entry-drawer");
  await expect(drawer).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText("32-A-");
  await expect(drawer).toContainText(/Change record|No changes/);
});

test("drill the bucket donut into sub-heads and back", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.goto("/field/spend");
  const panel = page.getByTestId("panel-bucket-donut");
  const first = panel.getByRole("listitem").first().getByRole("button");
  const bucket = (await first.textContent()) ?? "";
  await first.click();
  await expect(page).toHaveURL(/bucket=\d+/);
  await expect(page.getByTestId("drill-crumb")).toBeVisible();
  await expect(panel.getByRole("heading")).toContainText("sub-heads");
  const spend = await page.getByTestId("kpi-spend").innerText();
  const kpi = /₹[\d,]+/.exec(spend ?? "")?.[0];
  expect(kpi).toBeTruthy();
  expect(bucket).toContain(kpi!);                                    // the whole page is filtered to that bucket
  await page.getByTestId("drill-crumb").getByRole("button").click();
  await expect(page).not.toHaveURL(/bucket=/);
});

test("acknowledge, assign and close a report", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.goto("/field/reports");
  const card = page.getByTestId("report-card").filter({ hasText: "New" }).filter({ hasNotText: "Restricted" }).first();
  await card.click();
  const drawer = page.getByTestId("report-drawer");
  await expect(drawer).toBeVisible();
  await page.getByTestId("act-ack").click();
  await expect(drawer).toContainText("Acknowledged");
  await page.getByTestId("act-assign").click();
  const select = page.getByTestId("assign-select");
  await expect(select.locator("option").nth(1)).toBeAttached();
  await select.selectOption({ index: 1 });
  await page.getByTestId("assign-submit").click();
  await expect(drawer.getByText("Assigned", { exact: true }).first()).toBeVisible();
  await page.getByTestId("act-close").click();
  await page.getByTestId("close-note").fill("Visited and sorted out");
  await page.getByTestId("close-submit").click();
  await expect(drawer.getByText("Resolved", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("timeline")).toContainText("Status changed: Resolved");
});

test("view as a worker shows only their own data", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.getByTestId("view-as").selectOption("usr-worker-a1");
  await expect(page.getByTestId("role-chip")).toHaveText("Representative");                  // translated role, not the code
  await page.getByTestId("nav-field").click();
  await expect(page.getByTestId("navlink-ledger")).toBeVisible();
  await expect(page.getByTestId("navlink-spend")).toHaveCount(0);
  await expect(page.getByTestId("navlink-balances")).toHaveCount(0);
  await page.goto("/field/ledger");
  const rows = page.getByTestId("ledger-row");
  await expect(rows.first()).toBeVisible();
  const texts = await rows.allInnerTexts();
  expect(texts.length).toBeGreaterThan(0);
  for (const t of texts) expect(t).toContain("क1");                  // every row involves worker A1
  await page.goto("/field/spend");                                    // not in their scope: sent home
  await expect(page).not.toHaveURL(/\/field\/spend/);
});

test("language toggle switches Hindi and English", async ({ page }) => {
  await signIn(page, "usr-principal", { lang: "hi" });
  await page.goto("/field/reports");
  await expect(page.getByTestId("nav-overview")).toHaveText("अवलोकन");
  await expect(page.locator("html")).toHaveAttribute("lang", "hi");
  await page.getByTestId("lang-toggle").click();
  await expect(page.getByTestId("nav-overview")).toHaveText("Overview");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Reports");
  await page.getByTestId("lang-toggle").click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("रिपोर्ट");
});

const NAMESPACES = Object.keys(JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/i18n/en.json"), "utf8")));
const RAW_KEY = new RegExp(`\\b(?:${NAMESPACES.join("|")})\\.[a-z0-9_]+(?:\\.[a-z0-9_]+)*\\b`);

for (const lang of ["hi", "en"] as const) {
  test(`no missing translation keys on any screen (${lang})`, async ({ page }) => {
    await signIn(page, "usr-principal", { lang });
    for (const p of PAGES) {
      await page.goto(p);
      await page.waitForLoadState("networkidle");
      const text = await page.locator("body").innerText();
      expect(text, `raw key on ${p}`).not.toMatch(RAW_KEY);
      expect(text).not.toMatch(/project pulse/i);
    }
  });
}

for (const lang of ["hi", "en"] as const) {
  test(`header: nothing overlaps or disappears (${lang}, 1280 / 1440 / 1920)`, async ({ page }) => {
    await signIn(page, "usr-principal", { lang });
    for (const width of [1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of PAGES) {
        await page.goto(route);
        await expect(page.getByTestId("lang-toggle")).toBeVisible();
        expect(await headerProblems(page), `${lang} ${width} ${route}`).toEqual([]);
      }
      await page.getByTestId("nav-field").click();                    // the open menu must not push anything aside
      await expect(page.getByRole("menu")).toBeVisible();
      expect(await headerProblems(page), `${lang} ${width} menu open`).toEqual([]);
    }
  });
}

test("the Field group is named in one language at a time", async ({ page }) => {
  // the page title is the h1's own text, before the constituency qualifier
  const title = () => page.getByRole("heading", { level: 1 }).evaluate((h) => h.firstChild?.textContent?.trim());
  await signIn(page, "usr-principal", { lang: "hi" });
  await page.goto("/field");
  await expect(page.getByTestId("nav-field")).toHaveText(/^मैदान\s*▾?$/);
  expect(await title()).toBe("मैदान");
  await page.getByTestId("lang-toggle").click();
  await expect(page.getByTestId("nav-field")).toHaveText(/^Field\s*▾?$/);
  expect(await title()).toBe("Field");
});

test("team import: check shows each row, then import adds the members", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.goto("/field/team");
  await page.getByTestId("import-open").click();
  const header = "user_id,display_name,parent_user_id,finance_scope,can_give_advance,is_coordinator,areas,blocks\n";
  const text = page.getByTestId("import-text");
  await text.fill(header + "usr-worker-c1,Demo Worker C1,usr-incharge-b,own,no,no,area-c1,\nusr-nobody,Nobody,usr-principal,own,,,,\n");
  await page.getByTestId("import-check").click();
  await expect(page.getByTestId("import-result").locator('tr[data-ok="false"]')).toContainText("unknown user");
  await expect(page.getByTestId("import-run")).toBeDisabled();                  // nothing can be imported while a row is bad
  await text.fill(header + "usr-worker-c1,Demo Worker C1,usr-incharge-b,own,no,no,area-c1,\nusr-worker-c2,Demo Worker C2,usr-worker-c1,own,,,area-c2,\n");
  await page.getByTestId("import-check").click();
  await expect(page.getByTestId("import-result").locator('tr[data-ok="true"]')).toHaveCount(2);
  await page.getByTestId("import-run").click();
  await expect(page.getByTestId("import-done")).toContainText("2");
  await page.getByRole("button", { name: /close/i }).click();
  await expect(page.getByTestId("team-tree")).toContainText("Demo Worker C2");
});

test("ledger figures come from the server summary, not from the rows loaded", async ({ page }) => {
  await signIn(page, "usr-principal");
  await page.goto("/field/ledger");
  await expect(page.getByTestId("ledger-row").first()).toBeVisible();
  const summary = await page.evaluate(async () => {
    const r = await fetch("/demo/api/token", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "usr-principal" }) });
    const { token } = await r.json();
    const s = await fetch("/acs/32/field/txns/summary", { headers: { Authorization: `Bearer ${token}` } });
    return s.json();
  });
  const shown = (await page.getByTestId("ledger-total").innerText()).replace(/[^\d]/g, "");
  expect(shown).toBe(String(Math.round(Number(summary.totals.spend_total))));
  const rows = await page.getByTestId("ledger-row").count();
  expect(rows).toBeLessThanOrEqual(100);                       // paged for display only
  if (await page.getByTestId("ledger-more").isVisible()) {
    await page.getByTestId("ledger-more").click();
    await expect.poll(() => page.getByTestId("ledger-row").count()).toBeGreaterThan(rows);
    expect((await page.getByTestId("ledger-total").innerText()).replace(/[^\d]/g, "")).toBe(shown);
  }
});
