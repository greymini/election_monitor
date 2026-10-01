import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'
import { chartInk, partyColor } from '../lib/tokens'

/**
 * Cross-constituency comparison (spec 7.14): one table plus small multiples.
 *
 * This page is the reason `mv_ac_summary` exists. It is also the page where an
 * empty cell is most likely to be misread, so nothing is zero-filled: a
 * constituency with no Form 20 loaded shows a dash and is listed under the
 * table as not loaded, rather than appearing to have won by nothing.
 */

interface CompareRow {
  ac_id: number
  ac_number: number
  name_en: string
  name_hi: string
  verified: boolean
  election_label: string
  election_type: string
  year: number
  is_baseline: boolean
  winner_party: string | null
  runner_party: string | null
  margin_votes: number | null
  margin_pct: number | null
  signed_margin_pct: number | null
  turnout_pct: number | null
  electors: number | null
  valid_votes: number | null
  jlkm_share_pct: number | null
  new_voter_pct: number | null
  crosswalk_coverage_pct: number | null
  booths: number | null
}

interface CompareResponse {
  rows: CompareRow[]
  count: number
  note: string
}

interface AcListRow {
  ac_number: number
  name_en: string
  name_hi: string
  verified: boolean
  booths: number | null
  has_booth_data: boolean
  districts: string | null
  reservation: string
}

/** A dash, not a zero. An absent figure is absence. */
function cell(value: number | null | undefined, render: (v: number) => string) {
  if (value === null || value === undefined) {
    return <span style={{ color: 'var(--text-muted)' }} title="Not loaded">—</span>
  }
  return <>{render(value)}</>
}

export default function Compare() {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const ink = chartInk()

  const list = useQuery<{ acs: AcListRow[]; note: string }>({
    queryKey: ['acs'],
    queryFn: () => api.get('/acs'),
  })
  const series = useQuery<CompareResponse>({
    queryKey: ['compare'],
    queryFn: () => api.get('/compare'),
  })

  if (list.isLoading || series.isLoading) return <Loading />
  if (list.isError) return <ErrorState error={list.error} onRetry={() => void list.refetch()} />
  if (series.isError) {
    return <ErrorState error={series.error} onRetry={() => void series.refetch()} />
  }

  const acs = list.data?.acs ?? []
  const rows = series.data?.rows ?? []
  const name = (r: { name_en: string; name_hi: string }) => (hi ? r.name_hi : r.name_en)

  // Latest assembly row per constituency, for the table.
  const latest = new Map<number, CompareRow>()
  for (const row of rows) {
    const held = latest.get(row.ac_number)
    if (!held || row.year > held.year) latest.set(row.ac_number, row)
  }

  // One series per constituency for the margin small multiple.
  const byAc = new Map<number, CompareRow[]>()
  for (const row of rows) {
    byAc.set(row.ac_number, [...(byAc.get(row.ac_number) ?? []), row])
  }

  const notLoaded = acs.filter((a) => !a.has_booth_data)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold">{t('compare.heading')}</h1>
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {acs.length} {t('ac.label').toLowerCase()}
        </span>
      </div>

      <section className="card overflow-x-auto px-0 py-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-2xs" style={{ color: 'var(--text-muted)' }}>
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">{t('ac.label')}</th>
              <th className="px-3 py-2">{t('compare.marginTrend')}</th>
              <th className="px-3 py-2 text-right">{t('common.margin')}</th>
              <th className="px-3 py-2 text-right">%</th>
              <th className="px-3 py-2 text-right">{t('common.turnout')}</th>
              <th className="px-3 py-2 text-right">{t('common.electors')}</th>
              <th className="px-3 py-2 text-right">{t('compare.jlkmShare')}</th>
              <th className="px-3 py-2 text-right">{t('compare.newVoters')}</th>
              <th className="px-3 py-2 text-right">{t('common.booths')}</th>
            </tr>
          </thead>
          <tbody>
            {acs.map((ac) => {
              const row = latest.get(ac.ac_number)
              return (
                <tr key={ac.ac_number} className="border-t" style={{ borderColor: ink.grid }}>
                  <td className="px-3 py-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
                    {ac.ac_number}
                  </td>
                  <td className="px-3 py-2">
                    <span className="font-medium">{name(ac)}</span>
                    {ac.reservation !== 'GEN' && (
                      <span className="ml-1 text-3xs" style={{ color: 'var(--text-muted)' }}>
                        {ac.reservation}
                      </span>
                    )}
                    {!ac.verified && (
                      <span
                        className="ml-1.5 text-3xs"
                        style={{ color: 'var(--status-warn, var(--text-muted))' }}
                        title={t('ac.unverifiedHelp')}
                      >
                        {t('ac.unverified')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-2xs">
                    {row?.winner_party ? (
                      <>
                        <span style={{ color: partyColor(row.winner_party) }}>
                          {row.winner_party}
                        </span>
                        {row.runner_party && (
                          <span style={{ color: 'var(--text-muted)' }}>
                            {' '}
                            / {row.runner_party}
                          </span>
                        )}
                        <span style={{ color: 'var(--text-muted)' }}> {row.year}</span>
                      </>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">{cell(row?.margin_votes, num)}</td>
                  <td className="px-3 py-2 text-right">{cell(row?.margin_pct, pct)}</td>
                  <td className="px-3 py-2 text-right">{cell(row?.turnout_pct, pct)}</td>
                  <td className="px-3 py-2 text-right">{cell(row?.electors, num)}</td>
                  <td className="px-3 py-2 text-right">{cell(row?.jlkm_share_pct, pct)}</td>
                  <td className="px-3 py-2 text-right">{cell(row?.new_voter_pct, pct)}</td>
                  <td className="px-3 py-2 text-right">{cell(ac.booths, num)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      {rows.length === 0 ? (
        <Empty
          hint={`${t('compare.empty')} Load a Form 20 first: python -m ingest.parse_form20 <pdf> --election VS-2024 --load`}
        />
      ) : (
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{t('compare.marginTrend')}</h2>
          <div className="mt-2 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[...byAc.entries()].map(([acNumber, acRows]) => {
              const points = acRows
                .filter((r) => r.margin_pct !== null)
                .sort((a, b) => a.year - b.year)
                .map((r) => ({
                  year: String(r.year),
                  margin: r.margin_pct as number,
                  winner: r.winner_party ?? '',
                }))
              const label = name(acRows[0])
              return (
                <div key={acNumber}>
                  <div className="text-2xs font-medium">
                    {acNumber} · {label}
                  </div>
                  {points.length === 0 ? (
                    <div className="mt-1 text-3xs" style={{ color: 'var(--text-muted)' }}>
                      {t('ac.notLoaded')}
                    </div>
                  ) : (
                    <div className="mt-1 h-28">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={points} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                          <CartesianGrid vertical={false} stroke={ink.grid} />
                          <XAxis
                            dataKey="year"
                            tick={{ fontSize: 9, fill: ink.label }}
                            axisLine={{ stroke: ink.axis }}
                            tickLine={false}
                          />
                          <YAxis
                            tick={{ fontSize: 9, fill: ink.label }}
                            axisLine={false}
                            tickLine={false}
                            width={28}
                            unit="%"
                          />
                          <Tooltip
                            contentStyle={{
                              background: ink.surface,
                              border: `1px solid ${ink.grid}`,
                              borderRadius: 8,
                              fontSize: 11,
                              color: ink.text,
                            }}
                            formatter={(v: number, _n, item) => [
                              `${pct(v)} (${(item.payload as { winner: string }).winner})`,
                              t('common.margin'),
                            ]}
                          />
                          <Line
                            type="monotone"
                            dataKey="margin"
                            stroke={partyColor(points[points.length - 1].winner)}
                            strokeWidth={2}
                            dot={{ r: 3 }}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {notLoaded.length > 0 && (
        <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {t('ac.notLoaded')}{' '}
          {notLoaded.map((a) => `${a.ac_number} ${name(a)}`).join(', ')}.
        </p>
      )}

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {series.data?.note}
      </p>
    </div>
  )
}
