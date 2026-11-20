// Layout and language audit: every field page, both languages, both themes, three widths.
//   node e2e/audit.mjs [baseUrl] [--shots]      (against a running `python -m scripts.demo`)
// Reports, per screen: header items that overlap or are hidden, horizontal page overflow, text clipped
// by its box, and text in the wrong script (Latin words in Hindi mode, Devanagari in English mode).
// With --shots it also saves screenshots to web/test-results/audit/ (git-ignored).
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const args = process.argv.slice(2);
const base = args.find((a) => !a.startsWith("--")) ?? "http://127.0.0.1:8787";
const shots = args.includes("--shots");
const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "test-results", "audit");
const PAGES = ["/field", "/field/spend", "/field/ledger", "/field/balances", "/field/entry", "/field/register",
  "/field/reports", "/field/map", "/field/issues", "/field/team", "/field/settings"];
const WIDTHS = [1280, 1440, 1920];
// Each page, plus the overlays: the open Field menu and one drawer of each kind.
const VIEWS = [...PAGES.map((route) => ({ name: route.split("/").pop(), route })),
  { name: "field-menu", route: "/field", open: async (p) => { await p.getByTestId("nav-field").click(); await p.getByRole("menu").waitFor(); } },
  { name: "ledger-drawer", route: "/field/ledger", open: async (p) => { await p.getByTestId("ledger-row").first().click(); await p.getByTestId("entry-drawer").waitFor(); } },
  { name: "report-drawer", route: "/field/reports", open: async (p) => { await p.getByTestId("report-card").first().click(); await p.getByTestId("report-drawer").waitFor(); } },
  { name: "issue-drawer", route: "/field/issues", open: async (p) => { await p.getByTestId("issues-table").locator("tbody tr").first().click(); await p.getByTestId("issue-drawer").waitFor(); } }];

// Text people typed (member names, report and issue titles, notes, labels, keywords, statutory heads)
// is shown as recorded, in whatever script it was typed: it is data, not UI text. The audit fetches it
// from the API and ignores it when checking for wrong-script text.
async function typedText() {
  const { token } = await (await fetch(`${base}/demo/api/token`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: "usr-principal" }) })).json();
  const out = new Set();
  const TYPED_FIELD = /(^|_)(name|title|note|label|keyword|statutory_head|text|reason|description|remark)$/;
  const walk = (v, key = "") => {
    if (typeof v === "string") { if (TYPED_FIELD.test(key) && v.trim()) out.add(v.trim()); }
    else if (Array.isArray(v)) v.forEach((x) => walk(x, key));
    else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) walk(x, k);
  };
  for (const p of ["members", "reports?limit=200", "txns?limit=200", "issues", "keywords", "statutory-heads"]) {
    const r = await fetch(`${base}/acs/32/field/${p}`, { headers: { Authorization: `Bearer ${token}` } });
    if (r.ok) walk(await r.json());
  }
  return [...out].filter(Boolean).sort((a, b) => b.length - a.length);
}
const TYPED = await typedText();

// Latin tokens allowed in Hindi mode: units, codes, product and format names, ids in the sample data.
const LATIN_OK = /^(₹|AC|ID|CSV|PIN|QR|DEV|UPI|OK|PDF|URL|IST|km|GPS|Telegram|OpenStreetMap|Shift|Ctrl|Leaflet|Election|Monitor|[A-Z]?\d[\w\-:./]*|[a-z]+-[\w-]+|https?:.*|©.*)$/;

const browser = await chromium.launch({ channel: "msedge", headless: true });
let problems = 0;
for (const lang of ["hi", "en"]) for (const theme of ["dark", "light"]) for (const width of WIDTHS) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, locale: "hi-IN", timezoneId: "Asia/Kolkata" });
  await page.addInitScript(([l, t]) => { localStorage.setItem("em-lang", l); localStorage.setItem("em-theme", t); }, [lang, theme]);
  await page.goto(base + "/");
  await page.getByTestId("signin-usr-principal").click();
  await page.getByTestId("role-chip").waitFor();
  for (const { name, route, open } of VIEWS) {
    await page.goto(base + route);
    await page.waitForLoadState("networkidle");
    if (open) { await open(page); await page.waitForLoadState("networkidle"); }
    await page.waitForTimeout(400);
    const found = await page.evaluate(([lang, okSrc, typed]) => {
      const ok = new RegExp(okSrc);
      const out = [];
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
      // 1. header: every direct item visible, inside the viewport, and no two overlapping
      const header = document.querySelector("header");
      const items = [...header.querySelectorAll("[data-header-item]")];
      if (items.length < 13) out.push(`header: only ${items.length} tagged items found`);
      for (const el of items) {
        const r = el.getBoundingClientRect();
        if (!vis(el)) out.push(`header item hidden: ${el.dataset.headerItem}`);
        else if (r.right > innerWidth + 0.5 || r.left < -0.5) out.push(`header item off-screen: ${el.dataset.headerItem}`);
      }
      for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
        const a = items[i].getBoundingClientRect(), b = items[j].getBoundingClientRect();
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5)
          out.push(`header overlap: ${items[i].dataset.headerItem} / ${items[j].dataset.headerItem}`);
      }
      // 2. page wider than the viewport
      if (document.documentElement.scrollWidth > innerWidth + 1) out.push(`page overflows: ${document.documentElement.scrollWidth}px`);
      // 3. text clipped by its own box (overflow hidden, content wider), excluding scroll containers and deliberate truncation
      for (const el of document.querySelectorAll("body *")) {
        if (!vis(el) || el.closest(".leaflet-container") || el.closest("svg")) continue;
        const s = getComputedStyle(el);
        if (s.textOverflow === "ellipsis" || el.hasAttribute("data-clip-ok")) continue;
        const clipX = s.overflowX === "hidden" || s.overflowX === "clip";
        if (clipX && el.scrollWidth > el.clientWidth + 1 && el.textContent.trim())
          out.push(`clipped: <${el.tagName.toLowerCase()} class="${el.className}"> "${el.textContent.trim().slice(0, 40)}"`);
      }
      // 4. wrong script
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const bad = new Set();
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const el = n.parentElement;
        if (!el || !vis(el) || el.closest("[data-dev-only],.leaflet-control-attribution,script,style,option")) continue;
        const island = el.closest("[lang]");                      // text deliberately in the other language
        if (island && island !== document.documentElement) continue;
        let text = n.textContent.trim();
        for (const d of typed) if (text.includes(d)) text = text.split(d).join(" ");
        text = text.trim();
        if (!text) continue;
        if (lang === "en" && /[ऀ-ॿ]/.test(text)) bad.add(`hindi in en: "${text.slice(0, 50)}"`);
        if (lang === "hi") for (const w of text.split(/[\s,;()·/|—–:]+/)) {
          if (/[A-Za-z]{2,}/.test(w) && !ok.test(w)) { bad.add(`latin in hi: "${text.slice(0, 50)}"`); break; }
        }
      }
      return out.concat([...bad]);
    }, [lang, LATIN_OK.source, TYPED]);
    const tag = `${lang} ${theme} ${width} ${name}`;
    for (const f of found) { console.log(`${tag}: ${f}`); problems++; }
    if (shots) {
      const dir = path.join(OUT, `${lang}-${theme}-${width}`);
      mkdirSync(dir, { recursive: true });
      await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: !open });
    }
  }
  await page.close();
}
await browser.close();
console.log(problems ? `${problems} problem(s)` : "no problems");
process.exitCode = problems ? 1 : 0;
