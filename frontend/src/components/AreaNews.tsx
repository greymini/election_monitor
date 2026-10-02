import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import PartyChip from './PartyChip'
import { Empty, ErrorState, Loading } from './States'
import { api } from '../lib/api'
import { dateShort, num, pct } from '../lib/format'
import type { AcState } from '../lib/ac'

/**
 * News about one panchayat or ward around an election, for the map's area
 * panel and the booth drawer's News tab (GET /acs/{n}/areas/{id}/news).
 *
 * Most areas have no news of their own, so the API falls back area -> block ->
 * constituency -> Jharkhand and says which level it used; this says so in
 * words. The area's result sits beside the news as context: nothing here
 * claims a story moved a vote.
 */

type Level = 'area' | 'block' | 'ac' | 'state'

interface AreaNewsItem {
  news_id: number; published: string | null; source: string | null; title: string
  url: string; parties: string[] | null; issues: string[] | null; relevance: number | null
}

export interface AreaNewsResponse {
  area: { area_id: number; name_en: string; name_hi: string; kind: string
          block_id: number; block_en: string; block_hi: string }
  window: { basis: 'poll_date' | 'no_poll_date' | 'recent'; poll_date: string | null
            election_label: string | null; date_from: string; date_to: string }
  level: Level | null
  counts: Record<Level, number>
  rows: AreaNewsItem[]
  result: { booths: number; valid_votes: number | null; jmm_pct: number | null
            bjp_pct: number | null; turnout_pct: number | null } | null
  result_note: string | null
}

export function AreaNewsList({ ac, areaId, election }:
  { ac: AcState; areaId: number; election: string | null }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const query = useQuery<AreaNewsResponse>({
    queryKey: ['area-news', ac.acNumber, areaId, election],
    queryFn: () => api.get(ac.path(
      `/areas/${areaId}/news${election ? `?election_label=${encodeURIComponent(election)}` : ''}`)),
    enabled: ac.acNumber !== null,
  })
  if (query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const d = query.data
  if (!d) return null

  const areaName = hi ? d.area.name_hi : d.area.name_en
  const levelName = (level: Level) => ({
    area: areaName,
    block: hi ? d.area.block_hi : d.area.block_en,
    ac: ac.ac ? (hi ? ac.ac.name_hi : ac.ac.name_en) : t('news.thisAc'),
    state: t('news.jharkhand'),
  })[level]
  const lang = i18n.language
  const range = `${dateShort(d.window.date_from, lang)} – ${dateShort(d.window.date_to, lang)}`

  return (
    <div className="space-y-2">
      <p className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
        {d.window.basis === 'poll_date'
          ? t('areaNews.windowPoll', { election: d.window.election_label,
                                       poll: dateShort(d.window.poll_date, lang), range })
          : d.window.basis === 'no_poll_date'
            ? t('areaNews.windowNoDate', { election: d.window.election_label, range })
            : t('areaNews.windowRecent', { range })}
      </p>

      {d.level && d.level !== 'area' && (
        <p className="rounded px-2 py-1.5 text-2xs"
           style={{ background: 'var(--surface-2)', color: 'var(--text-secondary)' }}>
          {t('areaNews.fallback', { area: areaName, level: levelName(d.level) })}
        </p>
      )}
      <ul className="flex flex-wrap gap-x-3 text-3xs" style={{ color: 'var(--text-muted)' }}>
        {(['area', 'block', 'ac', 'state'] as const).map((level) => (
          <li key={level}>{levelName(level)}: {num(d.counts[level])}</li>
        ))}
      </ul>

      {!d.level && <Empty hint={t('areaNews.none')} />}
      <ul className="divide-y" style={{ borderColor: 'var(--gridline)' }}>
        {d.rows.map((item) => (
          <li key={item.news_id} className="py-1.5">
            <a href={item.url} target="_blank" rel="noopener noreferrer"
               className="text-xs font-medium underline-offset-2 hover:underline">
              {item.title}
            </a>
            <div className="mt-0.5 flex flex-wrap items-center gap-1 text-3xs"
                 style={{ color: 'var(--text-muted)' }}>
              <span>{item.source} · {dateShort(item.published, lang)}</span>
              {(item.parties ?? []).map((p) => (
                <span key={p} className="chip"><PartyChip abbr={p} /></span>
              ))}
              {(item.issues ?? []).filter((i) => i !== 'other').map((i) => (
                <span key={i} className="chip">{t(`news.issue.${i}`, { defaultValue: i })}</span>
              ))}
            </div>
          </li>
        ))}
      </ul>

      {election && (
        <section className="rounded px-2 py-1.5 text-2xs" style={{ background: 'var(--surface-2)' }}>
          <h3 className="font-semibold">{t('areaNews.resultHeading', { election })}</h3>
          {d.result ? (
            <p>
              {t('areaNews.result', { booths: num(d.result.booths),
                                      jmm: pct(d.result.jmm_pct), bjp: pct(d.result.bjp_pct),
                                      turnout: pct(d.result.turnout_pct) })}
            </p>
          ) : (
            <p style={{ color: 'var(--text-muted)' }}>{t('areaNews.noResult')}</p>
          )}
          <p className="mt-0.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('areaNews.contextNotCause')}
          </p>
        </section>
      )}
    </div>
  )
}

/** The map's area panel: a side drawer like the booth drawer. */
export function AreaNewsDrawer({ ac, area, election, onClose }: {
  ac: AcState
  area: { area_id: number; name_en?: string; name_hi?: string | null }
  election: string | null
  onClose: () => void
}) {
  const { t, i18n } = useTranslation()
  const name = (i18n.language === 'hi' && area.name_hi ? area.name_hi : area.name_en) ?? ''
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <div className="fixed inset-0 z-[1200] flex justify-end overflow-hidden" role="dialog"
         aria-modal="true" aria-label={t('areaNews.heading', { area: name })}
         data-testid="area-drawer">
      <button className="flex-1 bg-black/25" aria-label={t('common.close')} onClick={onClose} />
      <div className="flex w-full min-w-0 max-w-md flex-col overflow-y-auto overflow-x-hidden p-3"
           style={{ background: 'var(--plane)' }}>
        <div className="mb-2 flex items-start justify-between gap-2">
          <h2 className="text-base font-semibold">{t('areaNews.heading', { area: name })}</h2>
          <button className="btn px-2 py-1 text-2xs" onClick={onClose}>{t('common.close')}</button>
        </div>
        <AreaNewsList ac={ac} areaId={area.area_id} election={election} />
      </div>
    </div>,
    document.body,
  )
}
