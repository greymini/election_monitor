import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import DataTable, { type Column } from '../components/DataTable'
import { Empty, ErrorState, Loading } from '../components/States'
import { api, downloadCsv } from '../lib/api'
import { num, pct, signedNum } from '../lib/format'
import { chartInk, token } from '../lib/tokens'

interface Row {
  booth_uid: string; revision: string; revision_date: string; is_post_sir: boolean
  area_hi: string; area_en: string; block_id: number
  additions: number; deletions: number; modifications: number
  add_18_19: number | null; add_female: number | null
  del_death: number | null; del_shifted: number | null
  electors: number | null; additions_pct: number | null; deletions_pct: number | null
  [key: string]: unknown
}

interface Revision { revision_id: number; label: string; revision_date: string; is_post_sir: boolean }

export default function Voters() {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [revision, setRevision] = useState('')
  const ink = chartInk()

  const revisions = useQuery<{ rows: Revision[] }>({
    queryKey: ['roll-revisions'], queryFn: () => api.get('/rolls/revisions'),
  })
  const query = useQuery<{ rows: Row[] }>({
    queryKey: ['roll-changes', revision],
    queryFn: () =>
      api.get(`/rolls/changes${revision ? `?revision_label=${encodeURIComponent(revision)}` : ''}`),
  })

  const rows = query.data?.rows ?? []
  const postSir = rows.some((r) => r.is_post_sir)

  /** Additions and deletions per area. Deletions render below the axis so the
   *  net reads at a glance - one measure (voters moving on or off the roll) on
   *  one axis, never two scales. */
  const byArea = useMemo(() => {
    const map = new Map<string, { area: string; additions: number; deletions: number }>()
    for (const row of rows) {
      const key = hi ? row.area_hi : row.area_en
      const entry = map.get(key) ?? { area: key, additions: 0, deletions: 0 }
      entry.additions += row.additions ?? 0
      entry.deletions += row.deletions ?? 0
      map.set(key, entry)
    }
    return [...map.values()]
      .sort((a, b) => b.additions - a.additions)
      .slice(0, 20)
      .map((e) => ({ ...e, deletions: -e.deletions }))
  }, [rows, hi])

  const columns = useMemo<Column<Row>[]>(() => [
    { key: 'booth_uid', header: t('common.booth'), width: '6rem' },
    { key: 'area', header: t('common.area'),
      render: (r) => (hi ? r.area_hi : r.area_en),
      value: (r) => (hi ? r.area_hi : r.area_en) },
    { key: 'revision', header: 'Revision',
      render: (r) => (<>{r.revision}{r.is_post_sir && <span className="chip ml-1">SIR</span>}</>) },
    { key: 'electors', header: t('common.electors'), numeric: true, render: (r) => num(r.electors) },
    { key: 'additions', header: t('voters.additions'), numeric: true, render: (r) => num(r.additions) },
    { key: 'additions_pct', header: '%', numeric: true, render: (r) => pct(r.additions_pct) },
    { key: 'deletions', header: t('voters.deletions'), numeric: true, render: (r) => num(r.deletions) },
    { key: 'deletions_pct', header: '%', numeric: true, render: (r) => pct(r.deletions_pct) },
    { key: 'net', header: t('voters.net'), numeric: true,
      render: (r) => signedNum((r.additions ?? 0) - (r.deletions ?? 0)),
      value: (r) => (r.additions ?? 0) - (r.deletions ?? 0) },
    { key: 'add_18_19', header: '18-19', numeric: true, render: (r) => num(r.add_18_19) },
    { key: 'add_female', header: 'Female', numeric: true, render: (r) => num(r.add_female) },
    { key: 'del_death', header: 'Died', numeric: true, render: (r) => num(r.del_death) },
    { key: 'del_shifted', header: 'Shifted', numeric: true, render: (r) => num(r.del_shifted) },
  ], [t, hi])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('voters.heading')}</h1>
        <select className="field ml-auto" value={revision}
                onChange={(e) => setRevision(e.target.value)}>
          <option value="">{t('common.all')}</option>
          {(revisions.data?.rows ?? []).map((r) => (
            <option key={r.revision_id} value={r.label}>
              {r.label}{r.is_post_sir ? ' (SIR)' : ''}
            </option>
          ))}
        </select>
        <button className="btn" disabled={!rows.length}
                onClick={() => void downloadCsv('/rolls/changes', 'roll_changes.csv')}>
          {t('common.export')}
        </button>
      </div>

      {postSir && (
        <p className="text-2xs" style={{ color: 'var(--status-warning)' }}>
          ! {t('voters.sirNote')}
        </p>
      )}

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && !rows.length && (
        <Empty hint="No roll supplements are loaded yet. Run: python -m ingest.parse_roll <file> --supplement --load" />
      )}

      {byArea.length > 0 && (
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">
            {t('voters.additions')} / {t('voters.deletions')} — {t('common.area')}
          </h2>
          <div className="mt-2" style={{ height: Math.max(220, byArea.length * 24) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byArea} layout="vertical" stackOffset="sign"
                        margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                <CartesianGrid horizontal={false} stroke={ink.grid} />
                <XAxis type="number" tick={{ fontSize: 11, fill: ink.label }}
                       axisLine={{ stroke: ink.axis }} tickLine={false}
                       tickFormatter={(v: number) => num(Math.abs(v))} />
                <YAxis type="category" dataKey="area" width={140}
                       tick={{ fontSize: 11, fill: ink.label }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: 'var(--surface-2)' }}
                         contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                         borderRadius: 8, fontSize: 12, color: ink.text }}
                         formatter={(value: number, name: string) => [num(Math.abs(value)), name]} />
                <Bar dataKey="additions" name={t('voters.additions')} stackId="roll"
                     fill={token('--seq-5')} radius={[0, 4, 4, 0]} />
                <Bar dataKey="deletions" name={t('voters.deletions')} stackId="roll"
                     fill={token('--status-serious')} radius={[4, 0, 0, 4]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-3 text-2xs"
               style={{ color: 'var(--text-secondary)' }}>
            <span className="inline-flex items-center gap-1">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: token('--seq-5') }} />
              {t('voters.additions')}
            </span>
            <span className="inline-flex items-center gap-1">
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: token('--status-serious') }} />
              {t('voters.deletions')}, drawn below the axis
            </span>
          </div>
        </section>
      )}

      {rows.length > 0 && (
        <DataTable rows={rows} columns={columns}
                   initialSort={{ key: 'additions', dir: 'desc' }}
                   caption="Roll additions and deletions per booth" />
      )}
    </div>
  )
}
