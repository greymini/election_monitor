import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

import type { DataHealth } from '../components/DataHealth'
import DataStatus from '../components/DataStatus'
import OverviewInsights from '../components/OverviewInsights'
import { FixtureBanner, Missing, SourceLink, Value } from '../components/Provenance'
import StatTile from '../components/StatTile'
import { Empty, ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api, isFixtureMode } from '../lib/api'
import { dateShort, num, pct } from '../lib/format'
import { chartInk, partyColor } from '../lib/tokens'

/**
 * Constituency overview (spec §7.1).
 *
 * Two things this page has to get right, both of which the audited version got
 * wrong.
 *
 * **Every figure says where it came from.** A headline margin with no source is
 * a number a reader has to trust rather than check, and the whole design rests
 * on being able to trace any figure to a Form 20 page.
 *
 * **The margin chart uses loaded data.** The audited version assigned it the
 * `PUBLISHED_MARGINS` constant unconditionally - the variable deciding whether
 * real data existed was computed correctly and then ignored, so the bars never
 * moved after a Form 20 load and only the caption changed. The constant's own
 * comment said the fallback was "no longer used" at that point. It is now.
 */

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
  /** From metric_margin_pct on the server. Not derived here: the formula is
   *  defined once, in analytics/metric_sql.py. */
  margin_pct: number | null
  /** From metric_turnout_pct on the server, for the same reason. */
  turnout_pct: number | null
  has_results?: boolean
  source_doc?: string | null
  source_page?: number | null
}

interface Summary {
  constituency: {
    ac_number: number
    code: string
    name_en: string
    name_hi: string
    reservation: string
    verified: boolean
  }
  bypoll: {
    vacancy_date: string | null
    deadline: string | null
    days_to_deadline: number | null
    note: string
  }
  elections: ElectionRow[]
  baseline: {
    label: string; jmm: number; bjp: number; jlkm: number; nota: number
    electors: number; votes: number
  } | null
  data_health: DataHealth
  fixture?: string | null
}

/** Published assembly margins (HLD §1.1), from public reporting. Shown only
 *  while no Form 20 is loaded, and captioned as such. */
const PUBLISHED_MARGINS = [
  { year: '2014', margin: 9933, winner: 'BJP' },
  { year: '2019', margin: 15884, winner: 'JMM' },
  { year: '2024', margin: 3838, winner: 'JMM' },
]

export default function Overview({ ac, isAdmin = false }: {
  ac: AcState
  /** Whether to offer the link to Admin's data-source screen. Passed down
   *  rather than fetched again: App already holds the session. */
  isAdmin?: boolean
}) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const ink = chartInk()
  const query = useQuery<Summary>({
    queryKey: ['summary', ac.acNumber],
    queryFn: () => api.get(ac.path('/summary')),
    enabled: ac.acNumber !== null,
  })

  if (ac.acNumber === null || query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const data = query.data
  if (!data) return <Empty hint={t('ac.notLoaded')} />

  const loaded = data.elections.filter((e) => (e.booths ?? 0) > 0)
  const assembly = loaded.filter((e) => e.type === 'VS').sort((a, b) => a.year - b.year)

  const turnoutSeries = assembly
    .filter((e) => e.electors && e.votes)
    .map((e) => ({
      year: String(e.year),
      turnout: Math.round((1000 * (e.votes ?? 0)) / (e.electors ?? 1)) / 10,
    }))

  /** Electors growth across the loaded assembly elections, not a stated 15%. */
  const electorPoints = assembly.filter((e) => (e.electors ?? 0) > 0)
  const electorSpan = electorPoints.length >= 2
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

  const loadedMargins = assembly
    .filter((e) => e.margin_votes != null && e.winner_party)
    .map((e) => ({
      year: String(e.year),
      margin: e.margin_votes as number,
      winner: e.winner_party as string,
    }))
  const usingPublished = loadedMargins.length === 0
  const marginSeries = usingPublished ? PUBLISHED_MARGINS : loadedMargins

  // Where the figures on this page came from. Fixture mode is checked first
  // because it is true regardless of what the payload says, and captioning
  // invented data "From loaded Form 20 data" - which this page did - is the
  // single most misleading thing it could do.
  const sourceCaption = isFixtureMode()
    ? t('overview.fixtureSource')
    : usingPublished
      ? t('overview.publishedFallback')
      : t('overview.fromLoaded')
  const tightest = marginSeries.reduce<{ year: string; margin: number } | null>(
    (best, row) => (best === null || row.margin < best.margin ? row : best),
    null,
  )

  const baselineRow = data.elections.find((e) => e.is_baseline)

  return (
    <div className="space-y-4">
      <FixtureBanner note={data.fixture} />

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold">
          {hi ? data.constituency.name_hi : data.constituency.name_en}
          <span className="ml-2 text-sm font-normal" style={{ color: 'var(--text-muted)' }}>
            {data.constituency.code}
            {data.constituency.reservation !== 'GEN' && ` · ${data.constituency.reservation}`}
          </span>
        </h1>
        {!data.constituency.verified && (
          <span
            className="rounded px-1.5 py-0.5 text-2xs"
            style={{
              background: 'var(--status-warn-bg, var(--surface-2))',
              color: 'var(--status-warn, var(--text-secondary))',
            }}
            title={t('ac.unverifiedHelp')}
          >
            {t('ac.unverified')}
          </span>
        )}
      </div>

      {/* Headline tiles. Each shows its provenance, or a dash with a reason. */}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label={t('common.margin')}
          value={
            <Value
              value={baselineRow?.margin_votes}
              reason={t('overview.noResultsYet')}
              // Item 3. The headline was the bare number and the subline was a
              // whole sentence that wrapped to three lines in a narrow card,
              // with "fixture" trailing after it as loose text that read like
              // part of the sentence. Winner and margin are the headline now,
              // the rest is one short subline, and the fixture note is a badge
              // rendered by SourceLink.
              render={(v) => (
                baselineRow?.winner_party
                  ? `${baselineRow.winner_party} +${num(v)}`
                  : num(v)
              )}
            />
          }
          sub={
            <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
              {baselineRow?.winner_party ? (
                <span className="whitespace-nowrap">
                  {t('overview.marginSubline', {
                    pct: baselineRow.margin_pct == null
                      ? '—'
                      : pct(baselineRow.margin_pct, 2),
                    runner: baselineRow.runner_party ?? t('common.noRunnerUp'),
                  })}
                </span>
              ) : (
                <span>{t('overview.noResultsYet')}</span>
              )}
              {baselineRow?.label && (
                <span className="whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                  {baselineRow.label}
                </span>
              )}
              <SourceLink
                doc={baselineRow?.source_doc}
                page={baselineRow?.source_page ?? null}
              />
            </span>
          }
        />
        <StatTile
          label={t('common.electors')}
          value={
            <Value
              value={baselineRow?.electors}
              reason={t('overview.noRollLinked')}
              render={(v) => num(v)}
            />
          }
          sub={electorGrowthPct !== null && electorSpan
            ? t('overview.electorCaption', {
              from: electorSpan.from, to: electorSpan.to, pct: pct(electorGrowthPct),
            })
            : t('overview.electorCaptionPlain')}
        />
        <StatTile
          label={t('common.turnout')}
          value={
            <Value
              // Was computed here as votes/electors, from a vote count that
              // excluded NOTA - so it disagreed with METRICS.md and with the
              // booth table. The server returns metric_turnout_pct now.
              value={baselineRow?.turnout_pct ?? null}
              reason={t('overview.noRollLinked')}
              render={(v) => pct(v)}
            />
          }
          sub={baselineRow?.label ?? ''}
        />
        <StatTile
          label={t('overview.bypollIn')}
          value={
            <Value
              value={data.bypoll.days_to_deadline}
              reason={data.bypoll.note}
              // Item 1. This was `${num(v)} ${t('overview.daysLeft')}`, and the
              // key is "{{count}} days left" - so it rendered
              // "157 {{count}} days left". `count` drives i18next's plural
              // selection and `formatted` carries the Indian-grouped digits,
              // because interpolation will not apply num() for us.
              render={(v) => t('overview.daysLeft', { count: v, formatted: num(v) })}
            />
          }
          sub={data.bypoll.deadline
            ? dateShort(data.bypoll.deadline, i18n.language)
            : data.bypoll.note}
        />
      </div>

      {/* Item 2. This was the full data-health strip: eight cards, row counts,
          and a shell command in each. That is an operator's screen on a
          reader's page - a strategist reading a margin does not need
          `python -m ingest.geocode`, and a block in-charge has no shell to run
          it in, so for most of the people here it was advice that could not be
          taken sitting above the analysis. The strip moved to Admin under Data
          sources, admin only; this is the same facts in a sentence. */}
      <DataStatus health={data.data_health} isAdmin={isAdmin} />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{t('overview.marginTrend')}</h2>
          {/* Item 4. This said "From loaded Form 20 data" in fixture mode -
              the one claim a fixture must never make, and directly contradicted
              by the banner at the top of the same page. */}
          <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
            {sourceCaption}
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
                    `${num(value)} (${(item.payload as { winner: string }).winner})`,
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
              ? t('overview.marginCaption', { year: tightest.year, votes: num(tightest.margin) })
              : t('overview.marginCaptionPlain')}
          </p>
        </section>

        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">{t('overview.electorTrend')}</h2>
          <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
            {sourceCaption}
          </p>
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
                {electorGrowthPct === null || !electorSpan
                  ? t('overview.electorCaptionPlain')
                  : t('overview.electorCaption', {
                    from: electorSpan.from, to: electorSpan.to, pct: pct(electorGrowthPct),
                  })}
              </p>
            </>
          ) : (
            <div className="mt-2">
              <Empty hint={`${t('overview.noRollLinked')} python -m ingest.parse_roll <pdf> --revision 2026-07 --date 2026-07-01 --load`} />
            </div>
          )}
        </section>
      </div>

      {/* Item 5. The Overview was four tiles, two charts and a table of
          elections: accurate, and not much use to someone deciding where to
          spend a day. These five answer questions the page is actually opened
          with, from endpoints that already exist, and each links to the page
          that answers it properly. */}
      <OverviewInsights ac={ac} />

      {/* Every contest, loaded or not, so absence is visible per election. */}
      <section className="card overflow-x-auto px-0 py-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-2xs" style={{ color: 'var(--text-muted)' }}>
              <th className="px-3 py-2">{t('common.election')}</th>
              <th className="px-3 py-2">{t('common.winner')}</th>
              <th className="px-3 py-2 text-right">{t('common.margin')}</th>
              <th className="px-3 py-2 text-right">{t('common.booths')}</th>
              <th className="px-3 py-2 text-right">{t('common.electors')}</th>
              <th className="px-3 py-2 text-right">{t('common.turnout')}</th>
              <th className="px-3 py-2">{t('common.source')}</th>
            </tr>
          </thead>
          <tbody>
            {data.elections.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-2xs"
                    style={{ color: 'var(--text-muted)' }}>
                  {t('ac.notLoaded')}
                </td>
              </tr>
            )}
            {data.elections.map((e) => (
              <tr key={e.label} className="border-t" style={{ borderColor: ink.grid }}>
                <td className="px-3 py-2">
                  {e.label}
                  {e.is_baseline && (
                    <span className="ml-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
                      {t('common.baseline')}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <Value value={e.winner_party} reason={t('overview.noResultsYet')}
                         render={(v) => (
                           <span style={{ color: partyColor(v) }}>{v}</span>
                         )} />
                </td>
                <td className="px-3 py-2 text-right">
                  <Value value={e.margin_votes} reason={t('overview.noResultsYet')}
                         render={(v) => num(v)} />
                </td>
                <td className="px-3 py-2 text-right">
                  <Value value={e.booths} reason={t('overview.noBoothsLoaded')}
                         render={(v) => num(v)} />
                </td>
                <td className="px-3 py-2 text-right">
                  <Value value={e.electors} reason={t('overview.noRollLinked')}
                         render={(v) => num(v)} />
                </td>
                <td className="px-3 py-2 text-right">
                  <Value
                    value={e.electors && e.votes
                      ? Math.round((1000 * e.votes) / e.electors) / 10 : null}
                    reason={t('overview.noRollLinked')}
                    render={(v) => pct(v)}
                  />
                </td>
                <td className="px-3 py-2">
                  {e.source_doc
                    ? <SourceLink doc={e.source_doc} page={null} />
                    : <Missing reason={t('prov.noSource')} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('overview.reviewNote')}
      </p>
    </div>
  )
}
