import { expect, type Page } from "@playwright/test";

/** Sign in through the dev sign-in page as a demo user, in a chosen language and theme. */
export async function signIn(page: Page, userId: string, opts: { lang?: "hi" | "en"; theme?: "dark" | "light" } = {}) {
  const { lang = "en", theme = "dark" } = opts;
  await page.addInitScript(([l, t]) => {
    if (!sessionStorage.getItem("em-init")) {
      localStorage.setItem("em-lang", l);
      localStorage.setItem("em-theme", t);
      sessionStorage.setItem("em-init", "1");
    }
  }, [lang, theme]);
  await page.goto("/");
  await page.getByTestId(`signin-${userId}`).click();
  await expect(page.getByTestId("role-chip")).toBeVisible();
}

/** Every field page a principal can open. */
export const PAGES = ["/field", "/field/spend", "/field/ledger", "/field/balances", "/field/entry", "/field/register",
  "/field/reports", "/field/map", "/field/issues", "/field/team", "/field/settings"];

/** Problems with the header at the current viewport: tagged items hidden, off-screen or overlapping. */
export async function headerProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const items = [...document.querySelectorAll<HTMLElement>("header [data-header-item]")];
    if (items.length < 13) out.push(`only ${items.length} header items`);
    const rects = items.map((el) => ({ id: el.dataset.headerItem ?? "?", r: el.getBoundingClientRect(), s: getComputedStyle(el) }));
    for (const { id, r, s } of rects) {
      if (r.width === 0 || r.height === 0 || s.visibility === "hidden") out.push(`${id} hidden`);
      else if (r.left < -0.5 || r.right > innerWidth + 0.5) out.push(`${id} off-screen`);
    }
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
      const a = rects[i].r, b = rects[j].r;
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5)
        out.push(`${rects[i].id} overlaps ${rects[j].id}`);
    }
    return out;
  });
}
