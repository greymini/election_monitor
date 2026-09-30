import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'

import DataTable, { type Column } from '../components/DataTable'
import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { CONFIDENCE_FLOOR, num, pct } from '../lib/format'
import { chartInk, partyColor, token } from '../lib/tokens'
import type { AcState } from '../lib/ac'

interface Row {
  booth_uid: string; area_id: number; area_hi: string; area_en: string
  community_en: string; community_hi: string; category: string
  est_count: number | null; est_pct: number; confidence: number; source: string
  [key: string]: unknown
}

export default function Caste({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [minConf, setMinConf] = useState(CONFIDENCE_FLOOR)
  const ink = chartInk()

  const query = useQuery<{ rows: Row[]; disclaimer: string }>({
    queryKey: ['caste', minConf],
    queryFn: () => api.get(ac.path(`/caste?min_conf=${minConf}`)),
    enabled: ac.acNumber !== null,
  })
  const rows = query.data?.rows ?? []

  const communities = useMemo(
    () => [...new Set(rows.map((r) => r.community_en))].sort(),
    [rows],
  )
  const [community, setCommunity] = useState<string>('')
  const active = community || communities[0] || ''

  /** Community share against JMM share, booth by booth. This is an ECOLOGICAL
   *  correlation: it describes booths, not people, and the caption says so. */
  const scatter = useMemo(
    () => rows.filter((r) => r.community_en === active).map((r) => ({
      x: r.est_pct,
      y: 0,
      booth: r.booth_uid,
      area: hi ? r.area_hi : r.area_en,
      confidence: r.confidence,
    })),
    [rows, active, hi],
  )

  const columns = useMemo<Column<Row>[]>(() => [
    { key: 'booth_uid', header: t('common.booth'), width: '6rem' },
    { key: 'area', header: t('common.area'),
      render: (r) => (hi ? r.area_hi : r.area_en),
      value: (r) => (hi ? r.area_hi : r.area_en) },
    { key: 'community', header: 'Community',
      render: (r) => (hi ? r.community_hi : r.community_en),
      value: (r) => (hi ? r.community_hi : r.community_en) },
    { key: 'category', header: 'Category' },
    { key: 'est_pct', header: t('common.estimate'), numeric: true,
      render: (r) => (
        <span style={{ color: r.confidence < CONFIDENCE_FLOOR ? 'var(--text-muted)' : undefined }}>
          {r.confidence < CONFIDENCE_FLOOR ? t('caste.insufficient') : pct(r.est_pct)}
        </span>
      ) },
    { key: 'est_count', header: t('common.electors'), numeric: true,
      render: (r) => num(r.est_count) },
    { key: 'confidence', header: t('common.confidence'), numeric: true,
      render: (r) => r.confidence.toFixed(2) },
    { key: 'source', header: t('common.source') },
  ], [t, hi])

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">{t('caste.heading')}</h1>

      <div className="card px-4 py-3" style={{ borderColor: 'var(--status-warning)' }}>
        <p className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
          <span aria-hidden style={{ color: 'var(--status-warning)' }}>! </span>
          {t('caste.disclaimer')}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {t('caste.minConfidence')}: <span className="tnum">{minConf.toFixed(2)}</span>
        </label>
        <input type="range" min={0} max={0.9} step={0.05} value={minConf}
               onChange={(e) => setMinConf(Number(e.target.value))} className="w-40" />
        {communities.length > 0 && (
          <select className="field ml-auto" value={active}
                  onChange={(e) => setCommunity(e.target.value)}>
            {communities.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
      </div>

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && !rows.length && (
        <Empty hint="No estimates above this confidence yet. Load a roll with python -m ingest.parse_roll, then run python -m analytics.caste_estimate." />
      )}

      {scatter.length > 0 && (
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{active} share per booth</h2>
          <div className="mt-2 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
                <CartesianGrid stroke={ink.grid} />
                <XAxis type="number" dataKey="x" name={`${active} %`} unit="%"
                       tick={{ fontSize: 11, fill: ink.label }} axisLine={{ stroke: ink.axis }}
                       tickLine={false}
                       label={{ value: `${active} share of booth (estimated)`, position: 'insideBottom',
                                offset: -12, fontSize: 11, fill: ink.label }} />
                <YAxis type="number" dataKey="y" hide domain={[-1, 1]} />
                <ZAxis type="number" dataKey="confidence" range={[40, 220]} name="confidence" />
                <Tooltip
                  cursor={{ strokeDasharray: '3 3', stroke: ink.axis }}
                  contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                  borderRadius: 8, fontSize: 12, color: ink.text }}
                  formatter={(value: number, name: string) =>
                    name === 'confidence' ? [value.toFixed(2), t('common.confidence')]
                                          : [pct(value), active]}
                  labelFormatter={() => ''}
                />
                <Scatter data={scatter} fill={partyColor('JMM')} fillOpacity={0.75}
                         stroke={token('--surface-1')} strokeWidth={2} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
            Marker size is the confidence of the estimate. Read any relationship with vote share as
            an ecological correlation between booths, never as how a community voted.
          </p>
        </section>
      )}

      {rows.length > 0 && (
        <DataTable rows={rows} columns={columns} initialSort={{ key: 'est_pct', dir: 'desc' }}
                   caption="Estimated community composition per booth" />
      )}
    </div>
  )
}
