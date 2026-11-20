// Saves a screenshot of every field screen, dark and light, into docs/screens/{dark,light}/.
//   node e2e/screens.mjs [baseUrl]        (against a running `python -m scripts.demo`)
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:8787";
// fileURLToPath, not URL.pathname: the repo path has a space, which pathname leaves as %20
const SCREENS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screens");
const PAGES = [["home", "/field"], ["spend", "/field/spend"], ["ledger", "/field/ledger"], ["balances", "/field/balances"],
  ["entry", "/field/entry"], ["register", "/field/register"], ["reports", "/field/reports"], ["map", "/field/map"],
  ["issues", "/field/issues"], ["team", "/field/team"], ["settings", "/field/settings"]];

const browser = await chromium.launch({ channel: "msedge", headless: true });
for (const theme of ["dark", "light"]) {
  const out = path.join(SCREENS, theme);
  mkdirSync(out, { recursive: true });
  const file = (name) => path.join(out, `${name}.png`);
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, locale: "hi-IN", timezoneId: "Asia/Kolkata" });
  await page.addInitScript((t) => { localStorage.setItem("em-theme", t); localStorage.setItem("em-lang", "hi"); }, theme);
  await page.goto(base + "/");
  await page.getByTestId("signin-usr-principal").waitFor();
  await page.screenshot({ path: file("signin") });
  await page.getByTestId("signin-usr-principal").click();
  await page.getByTestId("role-chip").waitFor();
  for (const [name, route] of PAGES) {
    await page.goto(base + route);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(700);
    await page.screenshot({ path: file(name), fullPage: true });
  }
  // one drawer of each kind
  await page.goto(base + "/field/ledger");
  await page.getByTestId("ledger-row").first().click();
  await page.getByTestId("entry-drawer").waitFor();
  await page.waitForTimeout(500);
  await page.screenshot({ path: file("ledger-drawer") });
  await page.goto(base + "/field/reports");
  await page.getByTestId("report-card").first().click();
  await page.getByTestId("report-drawer").waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: file("report-drawer") });
  await page.close();
  console.log("saved", out);
}
await browser.close();
