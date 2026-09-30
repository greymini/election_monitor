import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

import StatTile from '../components/StatTile'
import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { dateShort, num, pct } from '../lib/format'
import { chartInk, partyColor } from '../lib/tokens'
import type { AcState } from '../lib/ac'

interface ElectionRow {
  label: string
  type: string
  year: number
  is_baseline: boolean
  booths: number | null
  votes: number | null
  electors: number | null
  total_valid: number | null
  nota: number | null
  winner_party: string | null
  winner_votes: number | null
  runner_party: string | null
  runner_votes: number | null
  margin_votes: number | null
}

interface Summary {
  constituency: { code: string; name_en: string; name_hi: string; parent_pc: string }
  bypoll: { vacancy_date: string; deadline: string; days_to_deadline: number; note: string }
  elections: ElectionRow[]
  baseline: {
    label: string; jmm: number; bjp: number; jlkm: number; nota: number
    electors: number; votes: number
  } | null
  data_health: {
    booths: number; open_reviews: number; weak_crosswalks: number; latest_roll: string | null
  }
}

/** Published assembly margins (HLD 1.1). Secondary figures from public
 *  reporting, shown only while no Form 20 is loaded, and captioned as such.
 *
 *  This fallback used to be the chart's only input: `marginSeries` was assigned
 *  the constant unconditionally, so the bars never moved once real results were
 *  loaded and only the caption changed. The comment said the fallback was "no
 *  longer used" at that point; it was. It is now.
 */
const PUBLISHED_MARGINS = [
  { year: '2014', margin: 9933, winner: 'BJP' },
  { year: '2019', margin: 15884, winner: 'JMM' },
  { year: '2024', margin: 3838, winner: 'JMM' },
]

export default function Overview({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const ink = chartInk()
  const query = useQuery<Summary>({
    queryKey: ['summary', ac.acNumber],
    queryFn: () => api.get(ac.path('/summary')),
    enabled: ac.acNumber !== null,
  })

  if (query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const data = query.data!

  const loaded = data.elections.filter((e) => (e.booths ?? 0) > 0)
  const turnoutSeries = loaded
    .filter((e) => e.type === 'VS')
    .sort((a, b) => a.year - b.year)
    .map((e) => ({
      year: String(e.year),
      turnout: e.electors ? Number(((100 * (e.votes ?? 0)) / e.electors).toFixed(1)) : 0,
    }))

  /** Electors growth across the loaded assembly elections. The page used to
   *  state "about 15% between 2019 and 2024" as prose, which was right for the
   *  published figures and would have been wrong the moment a different pair of
   *  elections was loaded. */
  const electorPoints = loaded
    .filter((e) => e.type === 'VS' && (e.electors ?? 0) > 0)
    .sort((a, b) => a.year - b.year)
  const electorSpan =
    electorPoints.length >= 2
      ? {
          from: String(electorPoints[0].year),
          to: String(electorPoints[electorPoints.length - 1].year),
          first: electorPoints[0].electors as number,
          last: electorPoints[electorPoints.length - 1].electors as number,
        }
      : null
  const electorGrowthPct = electorSpan
    ? (100 * (electorSpan.last - electorSpan.first)) / electorSpan.first
    : null

  /** Margins from the loaded Form 20 data, assembly elections only. A row with
   *  no runner-up (an uncontested or part-loaded election) has no margin and is
   *  dropped rather than charted as zero. */
  const loadedMargins = loaded
    .filter((e) => e.type === 'VS' && e.margin_votes != null && e.winner_party)
    .sort((a, b) => a.year - b.year)
    .map((e) => ({
      year: String(e.year),
      margin: e.margin_votes as number,
      winner: e.winner_party as string,
    }))

  const usingPublished = loadedMargins.length === 0
  const marginSeries = usingPublished ? PUBLISHED_MARGINS : loadedMargins
  const tightest = marginSeries.reduce<{ year: string; margin: number } | null>(
    (best, row) => (best === null || row.margin < best.margin ? row : best),
    null,
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold">{t('overview.heading')}</h1>
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {data.constituency.code} ·{' '}
          {i18n.language === 'hi' ? data.constituency.name_hi : data.constituency.name_en} ·{' '}
          {data.constituency.parent_pc}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label={t('overview.bypollIn')}
          value={t('overview.daysLeft', { count: data.bypoll.days_to_deadline })}
          sub={`${dateShort(data.bypoll.deadline, i18n.language)} · ${data.bypoll.note}`}
          status={data.bypoll.days_to_deadline < 90 ? 'warning' : undefined}
          statusText={data.bypoll.days_to_deadline < 90 ? 'Announcement window' : undefined}
        />
        <StatTile
          label={t('common.booths')}
          value={num(data.data_health.booths)}
          sub={`${t('overview.latestRoll')}: ${dateShort(data.data_health.latest_roll, i18n.language)}`}
        />
        <StatTile
          label={t('overview.openReviews')}
          value={num(data.data_health.open_reviews)}
          status={data.data_health.open_reviews > 0 ? 'warning' : 'good'}
          statusText={data.data_health.open_reviews > 0 ? 'Needs attention' : 'Clear'}
        />
        <StatTile
          label={t('overview.weakCrosswalks')}
          value={num(data.data_health.weak_crosswalks)}
          sub="Multi-year comparisons for these booths are provisional"
          status={data.data_health.weak_crosswalks > 0 ? 'serious' : 'good'}
          statusText={data.data_health.weak_crosswalks > 0 ? 'Review before use' : 'Clear'}
        />
      </div>

      {data.baseline && (
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{data.baseline.label}</h2>
          <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {(['JMM', 'BJP', 'JLKM', 'NOTA'] as const).map((party) => {
              const votes = (data.baseline as unknown as Record<string, number>)[party.toLowerCase()] ?? 0
              const share = data.baseline!.votes ? (100 * votes) / data.baseline!.votes : 0
              return (
                <StatTile key={party} label={party} value={num(votes)} sub={pct(share)}
                          accent={partyColor(party)} />
              )
            })}
            <StatTile
              label={t('common.margin')}
              value={num(Math.abs(data.baseline.jmm - data.baseline.bjp))}
              sub={`${data.baseline.jmm >= data.baseline.bjp ? 'JMM' : 'BJP'} · ${pct(
                (100 * Math.abs(data.baseline.jmm - data.baseline.bjp)) / (data.baseline.votes || 1), 2,
              )}`}
            />
          </div>
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{t('overview.marginTrend')}</h2>
          <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
            {usingPublished
              ? 'Published figures — re-verify against Form 20 before relying on them'
              : 'From loaded Form 20 data'}
          </p>
          <div className="mt-2 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={marginSeries} margin={{ top: 18, right: 8, left: 0, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke={ink.grid} />
                <XAxis dataKey="year" tick={{ fontSize: 11, fill: ink.label }}
                       axisLine={{ stroke: ink.axis }} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: ink.label }} axisLine={false} tickLine={false}
                       tickFormatter={(v: number) => num(v)} width={58} />
                <Tooltip
                  cursor={{ fill: 'var(--surface-2)' }}
                  contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                  borderRadius: 8, fontSize: 12, color: ink.text }}
                  formatter={(value: number, _name, item) => [
                    `${num(value)} (${(item.payload as { winner: string }).winner} hold)`,
                    t('common.margin'),
                  ]}
                />
                <Bar dataKey="margin" radius={[4, 4, 0, 0]} maxBarSize={52}>
                  {marginSeries.map((row) => (
                    <Cell key={row.year} fill={partyColor(row.winner)} />
                  ))}
                  <LabelList dataKey="margin" position="top"
                             formatter={(v: number) => num(v)}
                             style={{ fontSize: 11, fill: ink.text }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
            {tightest
              ? t('overview.marginCaption', {
                  year: tightest.year,
                  votes: num(tightest.margin),
                })
              : t('overview.marginCaptionPlain')}
          </p>
        </section>

        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{t('overview.electorTrend')}</h2>
          {turnoutSeries.length ? (
            <>
              <div className="mt-2 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={turnoutSeries} margin={{ top: 16, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid vertical={false} stroke={ink.grid} />
                    <XAxis dataKey="year" tick={{ fontSize: 11, fill: ink.label }}
                           axisLine={{ stroke: ink.axis }} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: ink.label }} axisLine={false}
                           tickLine={false} width={46} unit="%" domain={[0, 100]} />
                    <Tooltip
                      contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                      borderRadius: 8, fontSize: 12, color: ink.text }}
                      formatter={(value: number) => [pct(value), t('common.turnout')]}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line type="monotone" dataKey="turnout" name={t('common.turnout')}
                          stroke={partyColor('INC')} strokeWidth={2} dot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
                {electorGrowthPct === null
                  ? t('overview.electorCaptionPlain')
                  : t('overview.electorCaption', {
                      from: electorSpan!.from,
                      to: electorSpan!.to,
                      pct: pct(electorGrowthPct),
                    })}
              </p>
            </>
          ) : (
            <div className="mt-2">
              <Empty hint="Turnout comes from loaded Form 20 results. Load the VS-2024 Form 20, run the crosswalk, then refresh analytics." />
            </div>
          )}
        </section>
      </div>

      <section className="card px-4 py-3">
        <h2 className="text-sm font-semibold">{t('common.election')}</h2>
        <div className="mt-2 overflow-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="th">{t('common.election')}</th>
                <th className="th" style={{ textAlign: 'right' }}>{t('common.booths')}</th>
                <th className="th" style={{ textAlign: 'right' }}>{t('common.electors')}</th>
                <th className="th" style={{ textAlign: 'right' }}>{t('common.votes')}</th>
              </tr>
            </thead>
            <tbody>
              {data.elections.map((row) => (
                <tr key={row.label}>
                  <td className="td">
                    {row.label}
                    {row.is_baseline && <span className="chip ml-1.5">baseline</span>}
                  </td>
                  <td className="td tnum" style={{ textAlign: 'right' }}>{num(row.booths)}</td>
                  <td className="td tnum" style={{ textAlign: 'right' }}>{num(row.electors)}</td>
                  <td className="td tnum" style={{ textAlign: 'right' }}>{num(row.votes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
