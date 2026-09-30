/** Indian-convention number formatting.
 *
 * 94042 must render as 94,042 and 304898 as 3,04,898 - the lakh grouping is
 * what readers here expect, and en-US grouping (304,898) reads as wrong.
 */

const INDIAN = new Intl.NumberFormat('en-IN')

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return INDIAN.format(Math.round(value))
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

export function signedNum(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${INDIAN.format(Math.abs(Math.round(value)))}`
}

export function dateShort(value: string | null | undefined, lang = 'hi'): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleDateString(lang === 'hi' ? 'hi-IN' : 'en-IN', {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

/** Confidence below 0.4 is too weak to show as a figure (HLD 5). */
export const CONFIDENCE_FLOOR = 0.4

export function confidenceBand(value: number | null | undefined): 'weak' | 'fair' | 'good' {
  if (value === null || value === undefined) return 'weak'
  if (value < CONFIDENCE_FLOOR) return 'weak'
  return value < 0.7 ? 'fair' : 'good'
}
