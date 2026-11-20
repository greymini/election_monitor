// Dev helper: sign in as a demo persona and screenshot pages.
//   node e2e/shot.mjs <baseUrl> <outDir> <persona> <theme> <lang> <path>[,<path>...]
import { chromium } from "@playwright/test";

const [base, out, persona = "usr-principal", theme = "dark", lang = "hi", paths = "/field"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: Number(process.env.EM_W ?? 1600), height: 1000 }, locale: "hi-IN", timezoneId: "Asia/Kolkata" });
await page.addInitScript(([t, l]) => { localStorage.setItem("em-theme", t); localStorage.setItem("em-lang", l); }, [theme, lang]);
await page.goto(base + "/");
await page.getByTestId(`signin-${persona}`).click();
await page.getByTestId("role-chip").waitFor();
for (const p of paths.split(",")) {
  await page.goto(base + p);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
  const name = (p.replace(/\//g, "_").replace(/^_/, "") || "root") + `-${theme}-${lang}`;
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  console.log("saved", name);
}
await browser.close();
