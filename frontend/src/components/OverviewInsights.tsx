import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Missing } from './Provenance'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { pct, signed } from '../lib/format'

/**
 * The analytical half of the Overview.
 *
 * Five sections, each answering a question a strategist actually opens this
 * page with, and each linking to the page that answers it properly. Built from
 * endpoints that already exist - `/booths` and `/caste` and `/news` - so this
 * adds no API surface.
 *
 * `/booths` carries the per-booth metrics the map colours by, which is what
 * makes the first three sections possible without new endpoints: priority,
 * swing and new-voter share are already on every feature. Sorting happens here
 * rather than server-side because the response is one constituency's booths,
 * a few hundred rows, already in memory for the map.
 *
 * Every list is short on purpose. A ten-row table a reader finishes is worth
 * more on an overview than a three-hundred-row table they scroll past; the full
 * ones are a click away and say so.
 */

interface BoothFeature {
  properties: Record<string, unknown>
}

interface BoothCollection {
  features: BoothFeature[]
  meta: { count: number }
}

interface CasteRow {
  community_en: string
  community_hi: string
  category: string
  est_pct: number | null
  confidence: number | null
}

interface NewsItem {
  title: string
  source: string
  published: string | null
  url?: string | null
}

const TOP_N = 10

/** A section with a heading and a link to the page that owns it. */
function Panel({
  title,
  to,
  linkLabel,
  children,
}: {
  title: string
  to: string
  linkLabel: string
  children: React.ReactNode
}) {
  return (
    <section className="card px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <Link
          to={to}
          className="text-2xs underline decoration-dotted"
          style={{ color: 'var(--text-secondary)' }}
        >
          {linkLabel}
        </Link>
      </div>
      <div className="mt-2">{children}</div>
    </section>
  )
}

/** A compact booth list: name, area, and one figure. */
function BoothList({
  rows,
  render,
  emptyReason,
}: {
  rows: Array<{ uid: string; area: string; value: number }>
  render: (value: number) => string
  emptyReason: string
}) {
  if (rows.length === 0) return <Missing reason={emptyReason} />
  return (
    <ol className="divide-y text-2xs" style={{ borderColor: 'var(--gridline)' }}>
      {rows.map((row, index) => (
        <li key={row.uid} className="flex items-baseline gap-2 py-1">
          <span className="tnum w-4 shrink-0" style={{ color: 'var(--text-muted)' }}>
            {index + 1}
          </span>
          <span className="shrink-0 font-medium">{row.uid}</span>
          <span className="min-w-0 flex-1 truncate" style={{ color: 'var(--text-muted)' }}>
            {row.area}
          </span>
          <span className="tnum shrink-0 text-right font-medium">{render(row.value)}</span>
        </li>
      ))}
    </ol>
  )
}

export default function OverviewInsights(
  { ac, seesCaste }: { ac: AcState; seesCaste: boolean },
) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'

  const booths = useQuery<BoothCollection>({
    queryKey: ['booths', ac.acNumber, 'overview'],
    queryFn: () => api.get(
      ac.path('/booths?metric=priority_score&election_label=VS-2024'),
    ),
    enabled: ac.acNumber !== null,
  })

  // Block users may not see community estimates: the API answers 403, so the
  // request is not made and the panel is not shown.
  const caste = useQuery<{ rows: CasteRow[] }>({
    queryKey: ['caste', ac.acNumber, 'overview'],
    queryFn: () => api.get(ac.path('/caste')),
    enabled: ac.acNumber !== null && seesCaste,
  })

  // The API returns `rows` (this read `items`, so the panel was always empty).
  // Unlabelled items are included: without an Anthropic key nothing is ever
  // labelled, and the crawl's place-name tagging is enough for "latest news".
  const news = useQuery<{ rows: NewsItem[] }>({
    queryKey: ['news', ac.acNumber, 'overview'],
    queryFn: () => api.get(ac.path('/news?include_unlabelled=true&limit=5')),
    enabled: ac.acNumber !== null,
  })

  const features = booths.data?.features ?? []

  /** Rank by a numeric property, dropping booths where it is NULL.
   *
   *  Dropping rather than treating NULL as zero is the whole point: a booth
   *  with no priority score is not the lowest-priority booth, and a "top ten"
   *  padded with unknowns is a list that misleads in both directions. */
  const rank = (key: string, descending = true, absolute = false) =>
    features
      .map((f) => ({
        uid: String(f.properties.booth_uid ?? ''),
        area: String((hi ? f.properties.area_hi : f.properties.area_en) ?? ''),
        raw: f.properties[key],
      }))
      .filter((r): r is { uid: string; area: string; raw: number } =>
        typeof r.raw === 'number' && Number.isFinite(r.raw))
      .map((r) => ({ ...r, value: r.raw, sortBy: absolute ? Math.abs(r.raw) : r.raw }))
      .sort((a, b) => (descending ? b.sortBy - a.sortBy : a.sortBy - b.sortBy))
      .slice(0, TOP_N)

  const priority = useMemo(() => rank('priority_score'), [features, hi])
  // Swing of the AC's contest party A (the API's `swing_pct`).
  const swings = useMemo(() => rank('swing_pct', true, true), [features, hi])
  const newVoters = useMemo(() => rank('new_voter_pct'), [features, hi])

  // /caste is one row per booth per community. Taking the top eight rows
  // showed eight copies of the same community; this averages each
  // community's estimate and confidence over the booths that have one.
  const communities = useMemo(() => {
    const by = new Map<string, { row: CasteRow; pct: number; conf: number; n: number }>()
    for (const r of caste.data?.rows ?? []) {
      if (r.est_pct === null) continue
      const acc = by.get(r.community_en) ?? { row: r, pct: 0, conf: 0, n: 0 }
      acc.pct += r.est_pct
      acc.conf += r.confidence ?? 0
      acc.n += 1
      by.set(r.community_en, acc)
    }
    return [...by.values()]
      .map(({ row, pct: total, conf, n }) => ({ ...row, est_pct: total / n, confidence: conf / n }))
      .sort((a, b) => (b.est_pct ?? 0) - (a.est_pct ?? 0))
      .slice(0, 8)
  }, [caste.data])

  const items = (news.data?.rows ?? []).slice(0, 5)

  const loading = booths.isLoading
  const noBooths = !loading && features.length === 0

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title={t('insights.priority')} to="/booths" linkLabel={t('insights.allBooths')}>
        <p className="mb-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
          {t('insights.priorityNote')}
        </p>
        {noBooths
          ? <Missing reason={t('overview.noBoothsLoaded')} />
          : (
            <BoothList
              rows={priority}
              render={(v) => v.toFixed(3)}
              emptyReason={t('insights.noPriority')}
            />
          )}
      </Panel>

      <Panel title={t('insights.swings')} to="/results" linkLabel={t('insights.allResults')}>
        <p className="mb-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
          {t('insights.swingsNote')}
        </p>
        {noBooths
          ? <Missing reason={t('overview.noBoothsLoaded')} />
          : (
            <BoothList
              rows={swings}
              render={(v) => `${signed(v, 2)} pts`}
              emptyReason={t('insights.noSwings')}
            />
          )}
      </Panel>

      <Panel title={t('insights.newVoters')} to="/voters" linkLabel={t('insights.allVoters')}>
        <p className="mb-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
          {t('insights.newVotersNote')}
        </p>
        {noBooths
          ? <Missing reason={t('overview.noBoothsLoaded')} />
          : (
            <BoothList
              rows={newVoters}
              render={(v) => pct(v, 2)}
              emptyReason={t('insights.noNewVoters')}
            />
          )}
      </Panel>

      {seesCaste && (
        <Panel title={t('insights.community')} to="/caste" linkLabel={t('insights.allCommunity')}>
          <p className="mb-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('insights.communityNote')}
          </p>
          {communities.length === 0
            ? <Missing reason={t('insights.noCommunity')} />
            : (
              <ul className="divide-y text-2xs" style={{ borderColor: 'var(--gridline)' }}>
                {communities.map((row) => (
                  <li key={row.community_en} className="flex items-baseline gap-2 py-1">
                    <span className="min-w-0 flex-1 truncate font-medium">
                      {hi ? row.community_hi : row.community_en}
                    </span>
                    <span className="shrink-0 text-3xs" style={{ color: 'var(--text-muted)' }}>
                      {row.category}
                    </span>
                    <span className="tnum shrink-0 font-medium">{pct(row.est_pct, 1)}</span>
                    {/* An estimate without its confidence is a number pretending
                        to be a count. Below 0.4 the figure is greyed rather than
                        hidden, matching the caste pages. */}
                    <span
                      className="tnum w-14 shrink-0 text-right text-3xs"
                      style={{
                        color: (row.confidence ?? 0) < 0.4
                          ? 'var(--status-critical, #d03b3b)'
                          : 'var(--text-muted)',
                      }}
                      title={t('insights.confidenceTitle')}
                    >
                      {row.confidence === null ? '—' : `±${((1 - row.confidence) * 100).toFixed(0)}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
        </Panel>
      )}

      <Panel title={t('insights.news')} to="/news" linkLabel={t('insights.allNews')}>
        {items.length === 0
          ? <Missing reason={t('insights.noNews')} />
          : (
            <ul className="divide-y text-2xs" style={{ borderColor: 'var(--gridline)' }}>
              {items.map((item) => (
                <li key={`${item.source}-${item.title}`} className="py-1">
                  <div className="truncate font-medium">{item.title}</div>
                  <div className="text-3xs" style={{ color: 'var(--text-muted)' }}>
                    {item.source}
                  </div>
                </li>
              ))}
            </ul>
          )}
      </Panel>
    </div>
  )
}
