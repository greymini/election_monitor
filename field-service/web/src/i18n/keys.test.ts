/** Fails on any missing translation: both languages have exactly the same keys, every literal key used
 * in code exists, and every dynamic key family (`txn_status.${status}` ...) is complete. */
import fs from "node:fs";
import path from "node:path";
import en from "./en.json";
import hi from "./hi.json";

type Tree = { [k: string]: string | Tree };

function flatten(t: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(t)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else Object.assign(out, flatten(v, key));
  }
  return out;
}

const HI = flatten(hi as Tree);
const EN = flatten(en as Tree);
const SRC = path.resolve(__dirname, "..");

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) return d.name === "test" ? [] : sources(p);
    return /\.(tsx?)$/.test(d.name) && !/\.test\./.test(d.name) ? [p] : [];
  });
}

// every value a dynamic key may take: keep in step with the API enums
export const DYNAMIC: Record<string, string[]> = {
  txn_status: ["posted", "pending_ack", "disputed", "void"],
  issue_status: ["open", "closed"],
  report_status: ["draft", "new", "acknowledged", "assigned", "in_progress", "resolved", "closed_no_action", "duplicate"],
  flag: ["large", "cash_over_limit", "sensitive_in_window", "backdated", "ack_overdue"],
  persona: ["principal", "coordinator", "incharge", "worker", "treasurer"],
  scope: ["none", "own", "team", "all"],
  "shell.role": ["admin", "strategist", "block", "rep"],
  "nav.core": ["overview", "results", "voters", "places", "politics", "analysis", "admin"],
  kind: ["expense", "advance", "return", "inflow", "adjustment", "expense_plural", "advance_plural", "return_plural",
    "inflow_plural", "adjustment_plural"],
  pay_mode: ["cash", "upi", "bank_transfer", "cheque", "credit", "in_kind"],
  fund_source: ["own", "family_business", "party", "supporter_cash", "supporter_in_kind", "advance", "own_pocket_reimbursable", "other"],
  payee: ["vendor", "individual", "institution", "group"],
  reach: ["person", "family", "village", "event"],
  band: ["0-500", "501-2000", "2001-10000", "10001-25000", "25001-100000", "100000+"],
  followup: ["done", "partial", "not_done"],
  audit: ["void", "reclassify", "ack", "dispute", "ceiling", "export", "remind", "flag"],
  period: ["7d", "30d", "month", "all", "custom"],
  "balances.part": ["spent", "passed_on", "returned", "balance"],
  "entry.warn": ["large", "cash_over_limit", "sensitive_in_window"],
  entry: ["receiver_queued", "receiver_not_linked"],
  ledger: ["by_day", "by_bucket", "by_member"],
  event: ["draft_opened", "item_added", "more_added", "closing_photo", "note_added", "area_set", "location_added",
    "category_set", "urgency_set", "filed", "media_stored", "media_failed", "media_purged", "media_viewed", "status_changed",
    "escalated", "issue_linked", "issue_unlinked", "merged_from", "merged_into", "more_opened", "occurred_at_set",
    "item_refused", "alert_not_delivered", "transcribed", "transcript_failed", "transcript_corrected", "title_set",
    "opened", "renamed", "report_linked", "report_unlinked", "closed"],
  reports: ["transcript_none", "transcript_queued", "transcript_failed", "transcript_done"],
  "reports.dup_reason": ["same_area_category_24h"],
  "team.scope_hint": ["none", "own", "team", "all"],
  "settings.key": ["large_entry", "pin_limit", "tail_threshold", "autofile_minutes", "ack_remind_hours", "ack_flag_hours",
    "followup_days", "chat_cleanup_hours", "media_retention_days", "void_retention_days"],
};

describe("translations", () => {
  it("Hindi and English have the same keys", () => {
    expect(Object.keys(HI).filter((k) => !(k in EN))).toEqual([]);
    expect(Object.keys(EN).filter((k) => !(k in HI))).toEqual([]);
  });

  it("no value is empty", () => {
    expect(Object.entries(HI).filter(([, v]) => !v.trim()).map(([k]) => k)).toEqual([]);
    expect(Object.entries(EN).filter(([, v]) => !v.trim()).map(([k]) => k)).toEqual([]);
  });

  it("every literal key used in code exists in both", () => {
    const used = new Set<string>();
    for (const file of sources(SRC)) {
      const text = fs.readFileSync(file, "utf8");
      for (const m of text.matchAll(/\bt\(\s*["'`]([a-z0-9_.]+)["'`]/g)) used.add(m[1]);
      for (const m of text.matchAll(/\b(?:key|labelKey|tKey):\s*["']([a-z0-9_]+\.[a-z0-9_.]+)["']/g)) used.add(m[1]);
    }
    expect(used.size).toBeGreaterThan(20);
    expect([...used].filter((k) => !(k in HI) || !(k in EN)).sort()).toEqual([]);
  });

  it("every dynamic key family is complete", () => {
    const missing: string[] = [];
    for (const [family, values] of Object.entries(DYNAMIC)) {
      for (const v of values) for (const [lang, dict] of [["hi", HI], ["en", EN]] as const) {
        if (!(`${family}.${v}` in dict)) missing.push(`${lang}:${family}.${v}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("the product is called Election Monitor on every screen", () => {
    const all = [...Object.values(HI), ...Object.values(EN)].join("\n");
    expect(all).not.toMatch(/project pulse/i);
    expect(HI["shell.brand"]).toBe("Election Monitor");
  });
});
