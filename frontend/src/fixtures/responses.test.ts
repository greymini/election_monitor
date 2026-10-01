import { describe, expect, it } from 'vitest'

import { fixtureFor } from './responses'

type Obj = Record<string, unknown>

describe('fixtureFor', () => {
  it('filters the map by block', () => {
    const all = fixtureFor('/acs/32/booths') as { features: unknown[] }
    const one = fixtureFor('/acs/32/booths?block_id=3203') as { features: unknown[] }
    expect(one.features.length).toBeGreaterThan(0)
    expect(one.features.length).toBeLessThan(all.features.length)
  })

  it('does not relabel 2024 figures as another election', () => {
    const r = fixtureFor('/acs/32/results/VS-2019/booths') as { rows: unknown[] }
    expect(r.rows).toEqual([])
    const map = fixtureFor('/acs/32/booths?election_label=VS-2019') as
      { features: Array<{ properties: Obj }> }
    expect(map.features.every((f) => f.properties.margin_pct === null)).toBe(true)
  })

  it('applies the caste confidence floor', () => {
    const rows = (fixtureFor('/acs/32/caste?min_conf=0.6') as { rows: Obj[] }).rows
    expect(rows.every((r) => Number(r.confidence) >= 0.6)).toBe(true)
  })

  it('answers the scenario POST, and a swing moves the margin', () => {
    const at = (s: number) =>
      (fixtureFor('/acs/32/scenario', 'POST', { sympathy_swing: s }) as
        { margin: { point: number } }).margin.point
    expect(at(0.05)).toBeGreaterThan(at(0))
    expect(at(-0.05)).toBeLessThan(at(0))
  })

  it('answers the review-queue resolve POST', () => {
    expect(fixtureFor('/acs/32/admin/review-queue/7', 'POST', { status: 'resolved' }))
      .toEqual({ updated: 1 })
  })

  it('gives booth rows the same swing fields as the live API', () => {
    const row = (fixtureFor('/acs/32/results/VS-2024/booths') as { rows: Obj[] }).rows[0]
    expect(row).toHaveProperty('swing_pct')
    expect(row).toHaveProperty('swing_party', 'JMM')
    expect(row).not.toHaveProperty('jmm_swing_pct')
  })
})
