import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import PartyChip from '../components/PartyChip'
import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { dateShort, num } from '../lib/format'
import { chartInk, partyColor } from '../lib/tokens'
import type { AcState } from '../lib/ac'

type Scope = 'ac' | 'state'
type Sort = 'relevance' | 'date'

interface Item {
  news_id: number; published: string | null; source: string | null; title: string
  summary_hi: string | null; summary_en: string | null
  issues: string[] | null; parties: string[] | null; persons?: string[] | null
  sentiment: number | null; url: string; similarity?: number | null
  scope?: Scope | null; relevance?: number | null; label_method?: 'rules' | 'llm' | null
}

interface Example { news_id: number; title: string; url: string; source: string | null; published: string | null }

interface Place {
  place?: string; ac_number?: number | null; name_en: string; name_hi: string
  kind?: string; items: number
}

interface Summary {
  since: string; days: number; scope: Scope
  totals: { items: number; political: number; with_party: number; rules_labelled: number
            llm_labelled: number; with_tone: number; unlabelled: number }
  last_crawled: string | null
  parties: Array<{ party: string; items: number; share_pct: number }>
  party_weeks: Array<{ week: string; party: string; items: number }>
  issues: Array<{ issue: string; items: number; examples: Example[] }>
  other_items: number
  places?: Place[]
  top: Item[]
}

const TREND_PARTIES = 5
const TOP_STORIES = 5

/** The summary in the reader's language, else the other one. Keyword-labelled
 *  items have none: the rules never write prose. */
const summaryOf = (item: Item, hi: boolean) =>
  (hi ? item.summary_hi ?? item.summary_en : item.summary_en ?? item.summary_hi) ?? null

/** Issue values are stable keys shared with the backend; the label is translated. */
function useIssueLabel() {
  const { t } = useTranslation()
  return (issue: string) => t(`news.issue.${issue}`, { defaultValue: issue })
}

export default function News({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const issueLabel = useIssueLabel()
  const [scope, setScopeState] = useState<Scope>('ac')
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [issue, setIssue] = useState('')
  const [party, setParty] = useState('')
  const [place, setPlace] = useState('')
  const [sort, setSort] = useState<Sort>('relevance')
  const acName = ac.ac ? (hi ? ac.ac.name_hi : ac.ac.name_en) : t('news.thisAc')
  // A place belongs to one constituency; it means nothing across Jharkhand.
  const setScope = (value: Scope) => { setScopeState(value); setPlace('') }

  const summary = useQuery<Summary>({
    queryKey: ['news-summary', ac.acNumber, scope],
    queryFn: () => api.get(ac.path(`/news/summary?days=30&scope=${scope}`)),
    enabled: ac.acNumber !== null,
  })
  const list = useQuery<{ rows: Item[]; issues: string[] }>({
    queryKey: ['news', ac.acNumber, scope, submitted, issue, party, place, sort],
    enabled: ac.acNumber !== null,
    queryFn: () => {
      const params = new URLSearchParams({ scope, sort, days: '30' })
      if (submitted) params.set('q', submitted)
      if (issue) params.set('issue', issue)
      if (party) params.set('party', party)
      if (place) params.set('place', place)
      return api.get(ac.path(`/news?${params.toString()}`))
    },
  })
  const rows = list.data?.rows ?? []
  const s = summary.data
  const acPlaces = scope === 'ac' ? (s?.places ?? []).filter((p) => p.place) : []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('nav.news')}</h1>
        <div className="flex gap-1" role="group" aria-label={t('news.scope')}>
          {(['ac', 'state'] as const).map((value) => (
            <button key={value} type="button" aria-pressed={scope === value}
                    className={`btn ${scope === value ? 'btn-primary' : ''}`}
                    onClick={() => setScope(value)}>
              {value === 'ac' ? acName : t('news.jharkhand')}
            </button>
          ))}
        </div>
        {s && (
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {t('news.totals', { items: num(s.totals.items), political: num(s.totals.political),
                                days: s.days })}
            {s.last_crawled && ` · ${t('news.lastCrawled', { date: dateShort(s.last_crawled, i18n.language) })}`}
          </span>
        )}
      </div>

      {summary.isLoading && <Loading />}
      {summary.isError && <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />}
      {s && s.totals.items > 0 && (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <TopStories items={s.top.slice(0, TOP_STORIES)} issueLabel={issueLabel} />
            <PartyPanel summary={s} />
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <IssuePanel summary={s} issueLabel={issueLabel} onPick={(i) => setIssue(i)} />
            <PlacesPanel summary={s}
                         onPick={(p) => {
                           if (p.place) setPlace(p.place)
                           else if (p.ac_number) { ac.setAc(p.ac_number); setScope('ac') }
                         }} />
          </div>
          {s.totals.rules_labelled > 0 && (
            <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('news.rulesNote')}</p>
          )}
        </>
      )}

      <section className="space-y-2">
        <form className="flex flex-wrap items-center gap-1.5"
              onSubmit={(e) => { e.preventDefault(); setSubmitted(q.trim()) }}>
          <h2 className="mr-auto text-sm font-semibold">{t('news.allNews')}</h2>
          <input className="field w-48" value={q} placeholder={t('common.search')}
                 aria-label={t('common.search')} onChange={(e) => setQ(e.target.value)} />
          <select className="field" value={party} aria-label={t('news.party')}
                  onChange={(e) => setParty(e.target.value)}>
            <option value="">{t('news.allParties')}</option>
            {(s?.parties ?? []).map((p) => <option key={p.party} value={p.party}>{p.party}</option>)}
          </select>
          <select className="field" value={issue} aria-label={t('news.issueLabel')}
                  onChange={(e) => setIssue(e.target.value)}>
            <option value="">{t('news.allIssues')}</option>
            {(list.data?.issues ?? []).map((i) => <option key={i} value={i}>{issueLabel(i)}</option>)}
          </select>
          {acPlaces.length > 0 && (
            <select className="field" value={place} aria-label={t('news.place')}
                    onChange={(e) => setPlace(e.target.value)}>
              <option value="">{t('news.allPlaces')}</option>
              {acPlaces.map((p) => (
                <option key={p.place} value={p.place}>{hi ? p.name_hi : p.name_en}</option>
              ))}
            </select>
          )}
          <select className="field" value={sort} aria-label={t('news.sort')}
                  onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="relevance">{t('news.sortRelevance')}</option>
            <option value="date">{t('news.sortDate')}</option>
          </select>
          <button className="btn btn-primary">{t('common.search')}</button>
        </form>

        {list.isLoading && <Loading />}
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.data && !rows.length && <Empty hint={t('news.empty')} />}
        {rows.map((item) => <NewsCard key={item.news_id} item={item} issueLabel={issueLabel} />)}
      </section>
    </div>
  )
}

function NewsCard({ item, issueLabel, compact = false }:
  { item: Item; issueLabel: (i: string) => string; compact?: boolean }) {
  const { t, i18n } = useTranslation()
  const text = summaryOf(item, i18n.language === 'hi')
  return (
    <article className={compact ? 'py-2' : 'card px-4 py-3'}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <a href={item.url} target="_blank" rel="noopener noreferrer"
           className="text-sm font-medium underline-offset-2 hover:underline">
          {item.title}
        </a>
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {item.source} · {dateShort(item.published, i18n.language)}
          {/* typeof, not !== null: an item without the field (fixture
              mode, or a date-ranked list) rendered "NaN% match". */}
          {typeof item.similarity === 'number'
            && ` · ${t('news.match', { pct: (item.similarity * 100).toFixed(0) })}`}
          {item.scope === 'state' && ` · ${t('news.stateWide')}`}
        </span>
      </div>
      {text && !compact && (
        <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>{text}</p>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {(item.parties ?? []).map((p) => (
          <span key={p} className="chip"><PartyChip abbr={p} /></span>
        ))}
        {(item.persons ?? []).map((p) => <span key={p} className="chip">{p}</span>)}
        {(item.issues ?? []).filter((i) => i !== 'other').map((i) => (
          <span key={i} className="chip">{issueLabel(i)}</span>
        ))}
        {item.label_method === 'rules' && !compact && (
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {t('news.keywordLabelled')}
          </span>
        )}
      </div>
    </article>
  )
}

function TopStories({ items, issueLabel }: { items: Item[]; issueLabel: (i: string) => string }) {
  const { t } = useTranslation()
  return (
    <section className="card px-4 py-3">
      <h2 className="text-sm font-semibold">{t('news.topStories')}</h2>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('news.topCaption')}</p>
      <div className="mt-1 divide-y" style={{ borderColor: 'var(--gridline)' }}>
        {items.map((item) => (
          <NewsCard key={item.news_id} item={item} issueLabel={issueLabel} compact />
        ))}
      </div>
    </section>
  )
}

function PlacesPanel({ summary, onPick }: { summary: Summary; onPick: (p: Place) => void }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const places = summary.places ?? []
  const max = Math.max(1, ...places.map((p) => p.items))
  const pickable = (p: Place) => !!p.place || !!p.ac_number
  return (
    <section className="card px-4 py-3">
      <h2 className="text-sm font-semibold">{t('news.wherePanel')}</h2>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {summary.scope === 'ac' ? t('news.whereCaptionAc') : t('news.whereCaptionState')}
      </p>
      {places.length === 0 ? (
        <p className="mt-2 text-sm" style={{ color: 'var(--text-muted)' }}>{t('news.noPlaces')}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {places.map((p) => (
            <li key={p.place ?? `ac-${p.ac_number ?? 'rest'}`}
                className="grid grid-cols-[9rem_1fr_2.5rem] items-center gap-2">
              {pickable(p) ? (
                <button type="button" onClick={() => onPick(p)}
                        className="truncate text-left text-xs font-medium underline-offset-2 hover:underline">
                  {hi ? p.name_hi : p.name_en}
                </button>
              ) : (
                <span className="truncate text-xs" style={{ color: 'var(--text-muted)' }}>
                  {hi ? p.name_hi : p.name_en}
                </span>
              )}
              <span className="h-2 rounded-full" style={{ background: 'var(--surface-2)' }}>
                <span className="block h-2 rounded-full"
                      style={{ width: `${(100 * p.items) / max}%`, background: 'var(--seq-4)' }} />
              </span>
              <span className="text-right text-xs tabular-nums">{num(p.items)}</span>
            </li>
          ))}
        </ul>
      )}
      {summary.scope === 'ac' && (
        <p className="mt-2 text-2xs" style={{ color: 'var(--text-muted)' }}>{t('news.panchayatPending')}</p>
      )}
    </section>
  )
}

function PartyPanel({ summary }: { summary: Summary }) {
  const { t, i18n } = useTranslation()
  const ink = chartInk()
  const max = Math.max(1, ...summary.parties.map((p) => p.items))
  const trendParties = useMemo(
    () => summary.parties.slice(0, TREND_PARTIES).map((p) => p.party), [summary.parties])

  // One row per week, one column per party, for the trend lines.
  const weeks = useMemo(() => {
    const byWeek = new Map<string, Record<string, number | string>>()
    for (const r of summary.party_weeks) {
      if (!trendParties.includes(r.party)) continue
      const row = byWeek.get(r.week) ?? { week: r.week }
      row[r.party] = r.items
      byWeek.set(r.week, row)
    }
    return [...byWeek.values()]
      .sort((a, b) => String(a.week).localeCompare(String(b.week)))
      .map((row) => Object.fromEntries([
        ['week', row.week], ...trendParties.map((p) => [p, row[p] ?? 0]),
      ]))
  }, [summary.party_weeks, trendParties])

  return (
    <section className="card px-4 py-3">
      <h2 className="text-sm font-semibold">{t('news.partyPosition')}</h2>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('news.partyCaption')}</p>
      {summary.parties.length === 0 ? (
        <p className="mt-2 text-sm" style={{ color: 'var(--text-muted)' }}>{t('news.noParties')}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {summary.parties.map((p) => (
            <li key={p.party} className="grid grid-cols-[4.5rem_1fr_6.5rem] items-center gap-2">
              <PartyChip abbr={p.party} size="md" />
              <span className="h-2.5 rounded-full" style={{ background: 'var(--surface-2)' }}>
                <span className="block h-2.5 rounded-full"
                      style={{ width: `${(100 * p.items) / max}%`, background: partyColor(p.party) }} />
              </span>
              <span className="text-right text-xs tabular-nums">
                {t('news.partyItems', { items: num(p.items), share: p.share_pct.toFixed(0) })}
              </span>
            </li>
          ))}
        </ul>
      )}
      {weeks.length > 1 && (
        <>
          <h3 className="mt-3 text-xs font-semibold">{t('news.weeklyTrend')}</h3>
          <div className="mt-1" style={{ height: 160 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={weeks} margin={{ top: 6, right: 12, left: -16, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke={ink.grid} />
                <XAxis dataKey="week" tick={{ fontSize: 11, fill: ink.label }}
                       tickFormatter={(w: string) => dateShort(w, i18n.language).replace(/\s*\d{4}$/, '')}
                       axisLine={{ stroke: ink.axis }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: ink.label }}
                       axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                         borderRadius: 8, fontSize: 12, color: ink.text }}
                         labelFormatter={(w: string) => t('news.weekOf', { date: dateShort(w, i18n.language) })} />
                {trendParties.map((p) => (
                  <Line key={p} type="monotone" dataKey={p} stroke={partyColor(p)} strokeWidth={2}
                        dot={false} isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  )
}

function IssuePanel({ summary, issueLabel, onPick }:
  { summary: Summary; issueLabel: (i: string) => string; onPick: (issue: string) => void }) {
  const { t } = useTranslation()
  const max = Math.max(1, ...summary.issues.map((i) => i.items))
  return (
    <section className="card px-4 py-3">
      <h2 className="text-sm font-semibold">{t('news.inTheNews')}</h2>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('news.issueCaption', { other: num(summary.other_items) })}
      </p>
      {summary.issues.length === 0 ? (
        <p className="mt-2 text-sm" style={{ color: 'var(--text-muted)' }}>{t('news.noIssues')}</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {summary.issues.slice(0, 8).map((i) => (
            <li key={i.issue}>
              <div className="grid grid-cols-[9rem_1fr_2.5rem] items-center gap-2">
                <button type="button" onClick={() => onPick(i.issue)}
                        className="truncate text-left text-xs font-medium underline-offset-2 hover:underline">
                  {issueLabel(i.issue)}
                </button>
                <span className="h-2 rounded-full" style={{ background: 'var(--surface-2)' }}>
                  <span className="block h-2 rounded-full"
                        style={{ width: `${(100 * i.items) / max}%`, background: 'var(--seq-5)' }} />
                </span>
                <span className="text-right text-xs tabular-nums">{num(i.items)}</span>
              </div>
              {i.examples.map((e) => (
                <a key={e.news_id} href={e.url} target="_blank" rel="noopener noreferrer"
                   className="mt-0.5 block truncate pl-2 text-2xs underline-offset-2 hover:underline"
                   style={{ color: 'var(--text-secondary)' }}>
                  {e.title}
                </a>
              ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
