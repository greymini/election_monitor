import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'

import { ConfidenceDot, FixtureBanner } from '../components/Provenance'
import { Empty, ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { pct } from '../lib/format'
import { chartInk, partyColor } from '../lib/tokens'

/**
 * Community share against party share (spec §7.7, audit F4).
 *
 * The audited page plotted community share on x and **a constant zero** on y
 * with the axis hidden - a one-dimensional strip plot - while its caption
 * carefully explained how to read a relationship with vote share that was never
 * on the chart. The ecological regression existed as SQL in the crib sheet and
 * was exposed by no endpoint.
 *
 * Two things this page must not let a reader do:
 *
 *   1. **Read individual behaviour off an aggregate.** Both axes are booth-level
 *      aggregates. A positive slope is equally consistent with the opposite
 *      behaviour at individual level - Simpson's paradox - and the caveat says
 *      so in those terms rather than as a vague disclaimer.
 *   2. **Forget that x is an estimate.** The community share is inferred from
 *      surnames, not counted. Each point carries its confidence, points below
 *      the 0.4 floor are drawn hollow, and the fitted line is computed from the
 *      usable points only.
 */

interface Row {
  booth_uid: string
  area_en: string
  community_pct: number | null
  confidence: number | null
  jlkm_share_pct: number | null
  jmm_share_pct: number | null
  bjp_share_pct: number | null
  electors: number | null
}

interface Response {
  rows: Row[]
  community: string
  party: string
  caveat: string
  fixture?: string | null
}

const PARTIES = ['JLKM', 'JMM', 'BJP'] as const
type Party = (typeof PARTIES)[number]

const CONFIDENCE_FLOOR = 0.4

/** Ordinary least squares on the usable points. Returns null if there are too
 *  few to fit, rather than drawing a line through two dots. */
function fit(points: Array<{ x: number; y: number }>) {
  if (points.length < 4) return null
  const n = points.length
  const sx = points.reduce((s, p) => s + p.x, 0)
  const sy = points.reduce((s, p) => s + p.y, 0)
  const sxx = points.reduce((s, p) => s + p.x * p.x, 0)
  const sxy = points.reduce((s, p) => s + p.x * p.y, 0)
  const denom = n * sxx - sx * sx
  if (Math.abs(denom) < 1e-9) return null
  const slope = (n * sxy - sx * sy) / denom
  const intercept = (sy - slope * sx) / n
  // Pearson r, so the caption can say how weak the relationship is.
  const my = sy / n
  const mx = sx / n
  const cov = points.reduce((s, p) => s + (p.x - mx) * (p.y - my), 0)
  const vx = Math.sqrt(points.reduce((s, p) => s + (p.x - mx) ** 2, 0))
  const vy = Math.sqrt(points.reduce((s, p) => s + (p.y - my) ** 2, 0))
  const r = vx > 0 && vy > 0 ? cov / (vx * vy) : 0
  return { slope, intercept, r, n }
}

export default function CasteScatter({ ac }: { ac: AcState }) {
  const { t } = useTranslation()
  const ink = chartInk()
  const [party, setParty] = useState<Party>('JLKM')

  const query = useQuery<Response>({
    queryKey: ['caste-correlation', ac.acNumber],
    queryFn: () => api.get(ac.path('/caste/correlation')),
    enabled: ac.acNumber !== null,
  })

  if (ac.acNumber === null || query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const rows = query.data?.rows ?? []
  const shareOf = (r: Row): number | null =>
    party === 'JLKM' ? r.jlkm_share_pct : party === 'JMM' ? r.jmm_share_pct : r.bjp_share_pct

  const points = rows
    .filter((r) => r.community_pct !== null && shareOf(r) !== null)
    .map((r) => ({
      x: r.community_pct as number,
      y: shareOf(r) as number,
      uid: r.booth_uid,
      area: r.area_en,
      confidence: r.confidence,
      usable: (r.confidence ?? 0) >= CONFIDENCE_FLOOR,
      electors: r.electors,
    }))

  const usable = points.filter((p) => p.usable)
  const line = fit(usable)
  const lineData = line
    ? [
      { x: Math.min(...usable.map((p) => p.x)), y: 0 },
      { x: Math.max(...usable.map((p) => p.x)), y: 0 },
    ].map((p) => ({ x: p.x, y: line.intercept + line.slope * p.x }))
    : []

  return (
    <div className="space-y-3">
      <FixtureBanner note={query.data?.fixture} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">{t('casteScatter.heading')}</h1>
        <select className="select text-2xs" value={party}
                onChange={(e) => setParty(e.target.value as Party)}
                aria-label={t('casteScatter.party')}>
          {PARTIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      {points.length === 0 ? (
        <Empty hint={`${t('card.noCaste')}`} />
      ) : (
        <section className="card px-4 py-3">
          <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {query.data?.community} · {party}
          </p>
          <div className="mt-2 h-80">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 12, right: 16, left: 4, bottom: 28 }}>
                <CartesianGrid stroke={ink.grid} />
                <XAxis
                  type="number" dataKey="x" name={t('casteScatter.xAxis')}
                  tick={{ fontSize: 11, fill: ink.label }} unit="%"
                  axisLine={{ stroke: ink.axis }}
                  label={{ value: t('casteScatter.xAxis'), position: 'insideBottom',
                           offset: -16, fontSize: 11, fill: ink.label }}
                />
                {/* The y axis the audited version hid because it had nothing
                    to put on it. */}
                <YAxis
                  type="number" dataKey="y" name={t('casteScatter.yAxis')}
                  tick={{ fontSize: 11, fill: ink.label }} unit="%"
                  axisLine={{ stroke: ink.axis }}
                  label={{ value: t('casteScatter.yAxis'), angle: -90,
                           position: 'insideLeft', fontSize: 11, fill: ink.label }}
                />
                <ZAxis type="number" dataKey="electors" range={[40, 260]} />
                <Tooltip
                  contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                  borderRadius: 8, fontSize: 12, color: ink.text }}
                  formatter={(value: number, name: string) => [pct(value), name]}
                  labelFormatter={() => ''}
                  content={({ payload }) => {
                    const p = payload?.[0]?.payload as (typeof points)[number] | undefined
                    if (!p) return null
                    return (
                      <div className="rounded px-2 py-1 text-2xs"
                           style={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                    color: ink.text }}>
                        <strong>{p.uid}</strong> · {p.area}
                        <br />
                        {t('casteScatter.community')}: {pct(p.x)}
                        <ConfidenceDot confidence={p.confidence} />
                        <br />
                        {party}: {pct(p.y)}
                        {!p.usable && (
                          <>
                            <br />
                            <span style={{ color: 'var(--status-warn, var(--text-muted))' }}>
                              {t('caste.insufficient')}
                            </span>
                          </>
                        )}
                      </div>
                    )
                  }}
                />
                {/* Usable and too-weak points are drawn as separate series so
                    the low-confidence ones are visibly hollow rather than
                    silently mixed in. */}
                <Scatter
                  name={t('casteScatter.usable')} data={usable}
                  fill={partyColor(party)} fillOpacity={0.75}
                />
                <Scatter
                  name={t('caste.insufficient')} data={points.filter((p) => !p.usable)}
                  fill="none" stroke="var(--text-muted)" strokeDasharray="2 2"
                />
                {line && (
                  <Scatter data={lineData} line={{ stroke: ink.axis, strokeWidth: 1.5 }}
                           shape={() => <g />} legendType="none" />
                )}
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          <p className="mt-1 text-2xs" style={{ color: 'var(--text-secondary)' }}>
            {line
              ? t('casteScatter.fit', {
                slope: line.slope.toFixed(2),
                r: line.r.toFixed(2),
                n: line.n,
              })
              : t('casteScatter.noFit')}
          </p>
          {points.length !== usable.length && (
            <p className="text-2xs" style={{ color: 'var(--status-warn, var(--text-muted))' }}>
              {t('casteScatter.excluded', { count: points.length - usable.length })}
            </p>
          )}
        </section>
      )}

      <section className="card px-4 py-3">
        <h2 className="text-sm font-semibold">{t('casteScatter.howToRead')}</h2>
        <p className="mt-1 text-2xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {query.data?.caveat}
        </p>
      </section>
    </div>
  )
}
