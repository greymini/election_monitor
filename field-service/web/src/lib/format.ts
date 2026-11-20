/** Number, money, date and missing-value formatting. Indian digit grouping everywhere (3,04,898). */

const IN = "en-IN";

/** Amounts arrive from the API as decimal strings (never floats); parse only for display and charts. */
export function toNumber(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 304898 -> "3,04,898"; keeps up to `digits` decimals. */
export function num(v: string | number | null | undefined, digits = 0): string | null {
  const n = toNumber(v);
  if (n === null) return null;
  return n.toLocaleString(IN, { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

/** ₹ with Indian grouping; paise shown only when present. */
export function inr(v: string | number | null | undefined): string | null {
  const n = toNumber(v);
  if (n === null) return null;
  const paise = Math.round(Math.abs(n) * 100) % 100 !== 0;
  // the sign goes before the rupee sign: −₹1,000, not ₹-1,000
  return (n < 0 ? "−" : "") + "₹" + Math.abs(n).toLocaleString(IN, { minimumFractionDigits: paise ? 2 : 0, maximumFractionDigits: 2 });
}

/** Compact ₹ for axes and chart labels: ₹1.2 लाख / ₹3.4 क., or 1.2L / 3.4Cr in English. */
export function inrShort(v: string | number | null | undefined, lang: string = "hi"): string | null {
  const n = toNumber(v);
  if (n === null) return null;
  const abs = Math.abs(n);
  const hi = lang.startsWith("hi");
  if (abs >= 1e7) return `₹${trim(n / 1e7)}${hi ? " क." : "Cr"}`;
  if (abs >= 1e5) return `₹${trim(n / 1e5)}${hi ? " लाख" : "L"}`;
  if (abs >= 1e3) return `₹${trim(n / 1e3)}${hi ? " हज़ार" : "k"}`;
  return inr(n);
}

function trim(x: number): string {
  return x.toLocaleString(IN, { maximumFractionDigits: x >= 100 ? 0 : 1 });
}

/** "12.5%" from a 0-100 value. */
export function pct(v: string | number | null | undefined, digits = 1): string | null {
  const n = toNumber(v);
  return n === null ? null : `${n.toLocaleString(IN, { maximumFractionDigits: digits })}%`;
}

const IST = "Asia/Kolkata";

/** Times are stored in UTC and shown in Asia/Kolkata. */
export function dateTime(iso: string | null | undefined, lang = "hi"): string | null {
  if (!iso) return null;
  return new Intl.DateTimeFormat(lang.startsWith("hi") ? "hi-IN" : "en-IN", {
    timeZone: IST, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(iso));
}

export function date(isoDate: string | null | undefined, lang = "hi"): string | null {
  if (!isoDate) return null;
  // a bare date (YYYY-MM-DD) is a calendar date in IST already: do not shift it
  const d = isoDate.length === 10 ? new Date(`${isoDate}T12:00:00+05:30`) : new Date(isoDate);
  return new Intl.DateTimeFormat(lang.startsWith("hi") ? "hi-IN" : "en-IN", {
    timeZone: IST, day: "numeric", month: "short", year: "numeric",
  }).format(d);
}

/** Today's date in IST as YYYY-MM-DD. */
export function todayIst(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: IST }).format(now);
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Whole minutes/hours/days since an instant, for "age" columns. */
export function ageMinutes(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
}
