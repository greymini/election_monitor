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
 *  neutral grey at zero. Domain is a percentage; 20 points saturates. */
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
