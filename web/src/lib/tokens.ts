/** Palette access for chart code.
 *
 * Charts read CSS custom properties rather than hard-coded hex so light and
 * dark themes resolve from one place (src/styles/tokens.css). Recharts needs
 * real colour strings, not var() references, so these read the computed value.
 */

export type PartyKey = 'JMM' | 'BJP' | 'JLKM' | 'AJSU' | 'INC' | 'RJD' | 'OTH' | 'NOTA'

const PARTY_VAR: Record<PartyKey, string> = {
  JMM: '--party-jmm',
  BJP: '--party-bjp',
  JLKM: '--party-jlkm',
  AJSU: '--party-ajsu',
  INC: '--party-inc',
  RJD: '--party-rjd',
  OTH: '--party-other',
  NOTA: '--party-nota',
}

/** Fallbacks matter: during SSR-less first paint and in tests getComputedStyle
 *  can return an empty string, and an empty fill renders black. */
const FALLBACK: Record<string, string> = {
  '--party-jmm': '#1a6b39',
  '--party-bjp': '#ff8c42',
  '--party-jlkm': '#6d3fc4',
  '--party-ajsu': '#c98500',
  '--party-inc': '#2a78d6',
  '--party-rjd': '#1baf7a',
  '--party-other': '#a8a69e',
  '--party-nota': '#52514e',
  '--text-muted': '#898781',
  '--text-secondary': '#52514e',
  '--gridline': '#e1e0d9',
  '--baseline': '#c3c2b7',
  '--surface-1': '#fcfcfb',
  '--div-mid': '#f0efec',
}

export function token(name: string): string {
  if (typeof window === 'undefined') return FALLBACK[name] ?? '#898781'
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || FALLBACK[name] || '#898781'
}

export function partyColor(abbr: string): string {
  const key = abbr?.toUpperCase() as PartyKey
  return token(PARTY_VAR[key] ?? '--party-other')
}

/** Diverging ramp for margin and swing: JMM lead (blue) to BJP lead (red),
 *  neutral grey at zero. Domain is a percentage; 20 points saturates.
 *
 *  Kept for the metrics that are not a contest between two named parties.
 *  Anything signed by a contest pair should use `divergingPartyColor`, so the
 *  ramp ends match the legend's party chips. */
const DIVERGING = [
  '--div-a5', '--div-a4', '--div-a3', '--div-a2', '--div-a1',
  '--div-mid',
  '--div-b1', '--div-b2', '--div-b3', '--div-b4', '--div-b5',
]

export function divergingColor(value: number | null | undefined, saturateAt = 20): string {
  if (value === null || value === undefined || Number.isNaN(value)) return token('--text-muted')
  const clamped = Math.max(-saturateAt, Math.min(saturateAt, value))
  // -saturateAt -> index 0 (strong JMM), 0 -> 5 (neutral), +saturateAt -> 10
  const index = Math.round(((clamped + saturateAt) / (2 * saturateAt)) * (DIVERGING.length - 1))
  return token(DIVERGING[index])
}

// ---------------------------------------------------------------------------
// The party-coloured diverging ramp
// ---------------------------------------------------------------------------

/** Parse `#rgb`, `#rrggbb` or `rgb(r, g, b)` into components. */
function parseColor(value: string): [number, number, number] {
  const text = value.trim()
  if (text.startsWith('#')) {
    const hex = text.slice(1)
    if (hex.length === 3) {
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16),
      ]
    }
    if (hex.length >= 6) {
      return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
      ]
    }
  }
  const match = text.match(/(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)/)
  if (match) {
    return [Number(match[1]), Number(match[2]), Number(match[3])]
  }
  return [137, 135, 129] // --text-muted, the same fallback token() uses
}

function toHex([r, g, b]: [number, number, number]): string {
  const clamp = (n: number) => Math.max(0, Math.min(255, Math.round(n)))
  return `#${[r, g, b].map((n) => clamp(n).toString(16).padStart(2, '0')).join('')}`
}

/** Mix two colours. `ratio` 0 gives `from`, 1 gives `to`.
 *
 *  Mixed in sRGB rather than a perceptual space. That is the cruder choice, and
 *  it is deliberate: the ends of this ramp must be *exactly* the party colours
 *  shown in the legend chips, and a perceptual interpolation that also corrects
 *  lightness would shift them. Identity at the ends matters more here than
 *  perfectly even steps in between. */
export function mixColor(from: string, to: string, ratio: number): string {
  const a = parseColor(from)
  const b = parseColor(to)
  const r = Math.max(0, Math.min(1, ratio))
  return toHex([
    a[0] + (b[0] - a[0]) * r,
    a[1] + (b[1] - a[1]) * r,
    a[2] + (b[2] - a[2]) * r,
  ])
}

/** WCAG relative luminance, 0 (black) to 1 (white). */
export function relativeLuminance(value: string): number {
  const [r, g, b] = parseColor(value).map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/**
 * The diverging ramp in the contest pair's own colours.
 *
 * Negative values run toward `leftParty`, positive toward `rightParty`, through
 * the neutral mid token at zero - so the ramp ends are the same colours as the
 * legend's party chips, for whichever pair this AC contests.
 *
 * **The colour-blindness objection, and why this is still safe.** The previous
 * ramp used a validated blue/red pair precisely because JMM green and BJP
 * saffron are hard to tell apart under red-green colour blindness. That
 * reasoning was sound, but it assumed a ramp that forces both arms to matched
 * lightness. These two are not matched: JMM's green has a relative luminance
 * around 0.13 and BJP's saffron around 0.42, roughly a threefold difference, so
 * the arms differ in *lightness* as well as hue and remain distinguishable when
 * hue is not available. `assertDistinguishable` below is what keeps that true
 * if a palette changes, and section 6 of the hardening brief requires it.
 */
export function divergingPartyColor(
  value: number | null | undefined,
  saturateAt: number,
  leftParty: string,
  rightParty: string,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return token('--text-muted')
  }
  const mid = token('--div-mid')
  const clamped = Math.max(-saturateAt, Math.min(saturateAt, value))
  const magnitude = saturateAt <= 0 ? 0 : Math.abs(clamped) / saturateAt
  const end = partyColor(clamped < 0 ? leftParty : rightParty)
  return mixColor(mid, end, magnitude)
}

/** Eleven swatches for the legend, matching the ramp exactly. */
export function divergingPartySteps(
  saturateAt: number,
  leftParty: string,
  rightParty: string,
  steps = 11,
): string[] {
  const out: string[] = []
  for (let i = 0; i < steps; i += 1) {
    const value = -saturateAt + (i / (steps - 1)) * 2 * saturateAt
    out.push(divergingPartyColor(value, saturateAt, leftParty, rightParty))
  }
  return out
}

/** How far apart two colours are in lightness, 0 to 1.
 *
 *  Used by the test that section 6 asks for: the two arms of the map ramp must
 *  be separable without hue. */
export function luminanceGap(a: string, b: string): number {
  return Math.abs(relativeLuminance(a) - relativeLuminance(b))
}

const SEQUENTIAL = ['--seq-1', '--seq-2', '--seq-3', '--seq-4', '--seq-5', '--seq-6', '--seq-7']

export function sequentialColor(value: number | null | undefined, max: number): string {
  if (value === null || value === undefined || Number.isNaN(value) || max <= 0) {
    return token('--text-muted')
  }
  const ratio = Math.max(0, Math.min(1, value / max))
  return token(SEQUENTIAL[Math.round(ratio * (SEQUENTIAL.length - 1))])
}

export const chartInk = () => ({
  grid: token('--gridline'),
  axis: token('--baseline'),
  label: token('--text-muted'),
  text: token('--text-secondary'),
  surface: token('--surface-1'),
})
