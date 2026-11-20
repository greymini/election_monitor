/** Colour follows the entity, never its rank: every top-level bucket and report category has a fixed
 * slot, so filtering never repaints the survivors. Values live in tokens.css (--em-cat-N). */

export const SLOTS = 12;
export const OTHER = "var(--em-cat-other)";
export const slot = (n: number) => `var(--em-cat-${n})`;

/** Top-level expense buckets in seed order (db/seeds/exp_buckets.json). OTHER is grey. */
const BUCKET_SLOTS: Record<string, number> = {
  EVENT: 1, TRAVEL: 2, FOOD: 3, TEAM: 4, PUBLICITY: 5, OFFICE: 6,
  SOCIAL: 7, ASSIST: 8, DONATE: 9, WORKS: 10, PARTY: 11,
};

/** Report categories (db/seeds/report_categories.json). Twelve get a slot; the rest share grey and
 * are folded into "Other" in charts (they keep their own rows in legends and tables). */
const CATEGORY_SLOTS: Record<string, number> = {
  ACCIDENT: 1, HEALTH: 2, LAW: 3, DEATH: 4, SERVICES: 5, SCHEME: 6,
  CORRUPT: 7, LAND: 8, EDU: 9, WORK: 10, FARM: 11, SOCIALEV: 12,
};

export function bucketColor(code: string | null | undefined): string {
  if (!code) return OTHER;
  const top = code.split(".")[0];
  const n = BUCKET_SLOTS[top];
  return n ? slot(n) : OTHER;
}

export function categoryColor(code: string | null | undefined): string {
  const n = code ? CATEGORY_SLOTS[code] : undefined;
  return n ? slot(n) : OTHER;
}

export const isFoldedCategory = (code: string | null | undefined) => !code || !CATEGORY_SLOTS[code];

/** Sub-heads of one bucket, when the donut is drilled into: categorical slots in the sub-head's fixed
 * sort order (identity, not magnitude); "no sub-head" is grey. */
export const subheadColor = (sortIndex: number | null) =>
  sortIndex === null || sortIndex < 0 ? OTHER : slot((sortIndex % SLOTS) + 1);

/** One-hue scale for magnitude (choropleth-like fills), light to dark in dark mode. */
export const seq = (i: number) => `var(--em-seq-${Math.min(5, Math.max(1, i + 1))})`;
