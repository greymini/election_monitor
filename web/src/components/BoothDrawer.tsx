import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { Estimate, FixtureBanner, Missing, SourceLink, Value } from './Provenance'
import { ErrorState, Loading } from './States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'
import { partyColor } from '../lib/tokens'

/**
 * The booth card (spec §7.4): everything about one booth, in tabs.
 *
 * Results (all years) · Voters · Community · News · Ground · Sources
 *
 * The Sources tab is not an afterthought. It is the tab that makes every other
 * number checkable: a booth card with a margin and no way to reach the Form 20
 * page it came from is a number a reader has to trust. Every figure elsewhere
 * on the card carries its own source link too, but the tab collects them so the
 * question "what is this card built from" has one answer.
 */

interface ResultRow {
  election_label: string
  election_type: string
  election_year: number
  electors: number | null
  valid_votes: number | null
  votes_polled: number | null
  turnout_pct: number | null
  jmm: number | null
  bjp: number | null
  jlkm: number | null
  others: number | null
  nota: number | null
  winner_party: string | null
  runner_party: string | null
  margin_votes: number | null
  margin_pct: number | null
  signed_margin_pct: number | null
  source_doc: string | null
  source_page: number | null
  ps_numbers: string | null
}

interface BoothCard {
  booth: {
    booth_uid: string
    ps_name_hi: string | null
    building: string | null
    village_or_locality: string | null
    current_ps_number: number | null
    geocode_conf: number | null
    area_en: string
    area_hi: string
    area_kind: string
    block_en: string
    block_hi: string
    block_id: number
  }
  results: ResultRow[]
  roll: Array<{
    revision: string; revision_date: string; electors: number
    male: number | null; female: number | null; other: number | null
    source_doc: string | null; source_page: number | null
  }>
  new_voters: { additions: number | null; new_voter_pct: number | null; null_reason: string | null }
  priority: {
    priority_score: number | null; priority_quartile: number | null
    inputs_used: string[]; weight_used: number | null
  }
  crosswalk: Array<{
    election_label: string; ps_number: number; confidence: number | null
    reviewed: boolean; match_method: string
  }>
  caste_estimate?: Array<{
    name_en: string; name_hi: string; est_pct: number | null
    confidence: number | null; source: string
  }>
  caste_note?: string
  caveats: string[]
  fixture?: string | null
}

type Tab = 'results' | 'voters' | 'community' | 'news' | 'ground' | 'sources'

export default function BoothDrawer(
  { boothUid, ac, onClose }: { boothUid: string; ac: AcState; onClose: () => void },
) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [tab, setTab] = useState<Tab>('results')

  const query = useQuery<BoothCard>({
    // Keyed by AC as well as uid: booth_uid carries its AC number now, so a
    // stale cache entry cannot surface another constituency's booth, and the
    // request has to be scoped or it hits the legacy redirect to AC-32.
    queryKey: ['booth-card', ac.acNumber, boothUid],
    queryFn: () => api.get(ac.path(`/booths/${boothUid}/card`)),
    enabled: ac.acNumber !== null,
  })
  const card = query.data

  const TABS: Array<{ key: Tab; label: string }> = [
    { key: 'results', label: t('card.results') },
    { key: 'voters', label: t('card.voters') },
    { key: 'community', label: t('card.community') },
    { key: 'news', label: t('card.news') },
    { key: 'ground', label: t('card.ground') },
    { key: 'sources', label: t('card.sources') },
  ]

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <button className="flex-1 bg-black/25" aria-label={t('common.close')} onClick={onClose} />
      <div className="w-full max-w-md overflow-auto p-3" style={{ background: 'var(--plane)' }}>
        <div className="mb-2 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">{boothUid}</h2>
            {card && (
              <p className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
                {card.booth.building}
                {' · '}
                {hi ? card.booth.area_hi : card.booth.area_en}
                {' · '}
                {card.booth.block_en}
              </p>
            )}
          </div>
          <button className="btn px-2 py-1 text-2xs" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>

        {query.isLoading && <Loading />}
        {query.isError && (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        )}

        {card && (
          <div className="space-y-3">
            <FixtureBanner note={card.fixture} />

            {/* Caveats first: a reader should meet the limits before the
                numbers, not after. */}
            {card.caveats.length > 0 && (
              <ul className="rounded px-2 py-1.5 text-3xs"
                  style={{ background: 'var(--surface-2)', color: 'var(--text-secondary)' }}>
                {card.caveats.map((c) => <li key={c}>• {c}</li>)}
              </ul>
            )}

            <div className="flex flex-wrap gap-1 border-b pb-1"
                 style={{ borderColor: 'var(--surface-2)' }}>
              {TABS.map((tb) => (
                <button
                  key={tb.key}
                  className={`btn px-2 py-0.5 text-3xs ${tab === tb.key ? 'btn-primary' : ''}`}
                  onClick={() => setTab(tb.key)}
                  aria-pressed={tab === tb.key}
                >
                  {tb.label}
                </button>
              ))}
            </div>

            {tab === 'results' && (
              card.results.length === 0
                ? <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                    {t('overview.noResultsYet')}
                  </p>
                : (
                  <div className="space-y-2">
                    {card.results.map((r) => (
                      <section key={r.election_label} className="card px-3 py-2">
                        <div className="flex items-baseline justify-between gap-2">
                          <strong className="text-sm">{r.election_label}</strong>
                          <SourceLink doc={r.source_doc} page={r.source_page} compact />
                        </div>
                        <table className="mt-1 w-full text-2xs">
                          <tbody>
                            {([['JMM', r.jmm], ['BJP', r.bjp], ['JLKM', r.jlkm],
                               ['OTH', r.others], ['NOTA', r.nota]] as const).map(
                              ([party, votes]) => (
                                <tr key={party}>
                                  <td style={{ color: partyColor(party) }}>{party}</td>
                                  <td className="tnum text-right">
                                    <Value value={votes} reason={t('card.notInThisPoll')}
                                           render={(v) => num(v)} />
                                  </td>
                                  <td className="tnum text-right"
                                      style={{ color: 'var(--text-muted)' }}>
                                    {votes !== null && r.valid_votes
                                      ? pct(Math.round((1000 * votes) / r.valid_votes) / 10)
                                      : ''}
                                  </td>
                                </tr>
                              ),
                            )}
                          </tbody>
                        </table>
                        <dl className="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-0.5 text-2xs">
                          <dt style={{ color: 'var(--text-muted)' }}>{t('common.margin')}</dt>
                          <dd className="tnum text-right">
                            <Value value={r.margin_pct} reason={t('booths.noMargin')}
                                   render={(v) => `${pct(v)} (${num(r.margin_votes ?? 0)})`} />
                          </dd>
                          <dt style={{ color: 'var(--text-muted)' }}>{t('common.electors')}</dt>
                          <dd className="tnum text-right">
                            <Value value={r.electors} reason={t('overview.noRollLinked')}
                                   render={(v) => num(v)} />
                          </dd>
                          <dt style={{ color: 'var(--text-muted)' }}>{t('common.turnout')}</dt>
                          <dd className="tnum text-right">
                            <Value value={r.turnout_pct} reason={t('overview.noRollLinked')}
                                   render={(v) => pct(v)} />
                          </dd>
                          <dt style={{ color: 'var(--text-muted)' }}>PS</dt>
                          <dd className="text-right">{r.ps_numbers ?? '—'}</dd>
                        </dl>
                      </section>
                    ))}
                  </div>
                )
            )}

            {tab === 'voters' && (
              <div className="space-y-2">
                {card.roll.length === 0 ? (
                  <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                    {t('overview.noRollLinked')}
                  </p>
                ) : card.roll.map((r) => (
                  <section key={r.revision} className="card px-3 py-2">
                    <div className="flex items-baseline justify-between">
                      <strong className="text-sm">{r.revision}</strong>
                      <SourceLink doc={r.source_doc} page={r.source_page} compact />
                    </div>
                    <dl className="mt-1 grid grid-cols-2 gap-x-2 text-2xs">
                      <dt style={{ color: 'var(--text-muted)' }}>{t('common.electors')}</dt>
                      <dd className="tnum text-right">{num(r.electors)}</dd>
                      <dt style={{ color: 'var(--text-muted)' }}>M / F / O</dt>
                      <dd className="tnum text-right">
                        {num(r.male ?? 0)} / {num(r.female ?? 0)} / {num(r.other ?? 0)}
                      </dd>
                    </dl>
                  </section>
                ))}
                <section className="card px-3 py-2">
                  <strong className="text-sm">{t('booths.newVoterShare')}</strong>
                  <dl className="mt-1 grid grid-cols-2 gap-x-2 text-2xs">
                    <dt style={{ color: 'var(--text-muted)' }}>{t('voters.additions')}</dt>
                    <dd className="tnum text-right">
                      <Value value={card.new_voters.additions}
                             reason={card.new_voters.null_reason ?? t('booths.noRoll')}
                             render={(v) => num(v)} />
                    </dd>
                    <dt style={{ color: 'var(--text-muted)' }}>%</dt>
                    <dd className="tnum text-right">
                      <Value value={card.new_voters.new_voter_pct}
                             reason={card.new_voters.null_reason ?? t('booths.noRoll')}
                             render={(v) => pct(v)} />
                    </dd>
                  </dl>
                </section>
              </div>
            )}

            {tab === 'community' && (
              <div className="space-y-2">
                {!card.caste_estimate?.length ? (
                  <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                    {t('card.noCaste')}
                  </p>
                ) : (
                  <>
                    <table className="w-full text-2xs">
                      <tbody>
                        {card.caste_estimate.map((c) => (
                          <tr key={c.name_en}>
                            <td>
                              {hi ? c.name_hi : c.name_en}
                              {c.name_en === 'UNMATCHED' && (
                                <span className="ml-1 text-3xs"
                                      style={{ color: 'var(--text-muted)' }}>
                                  {t('card.unmatchedNote')}
                                </span>
                              )}
                            </td>
                            <td className="tnum text-right">
                              <Estimate
                                value={c.est_pct}
                                confidence={c.confidence}
                                method={c.source}
                                reason={t('card.noCaste')}
                                render={(v) => pct(v)}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-3xs" style={{ color: 'var(--text-muted)' }}>
                      {card.caste_note}
                    </p>
                  </>
                )}
              </div>
            )}

            {tab === 'news' && (
              <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                {t('card.newsPending')}
              </p>
            )}

            {tab === 'ground' && (
              <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                {t('card.groundPending')}
              </p>
            )}

            {tab === 'sources' && (
              <div className="space-y-2">
                <section className="card px-3 py-2">
                  <strong className="text-sm">{t('card.documents')}</strong>
                  <ul className="mt-1 space-y-0.5 text-2xs">
                    {[...new Set(card.results.map((r) => r.source_doc).filter(Boolean))]
                      .map((doc) => (
                        <li key={doc as string}>
                          <SourceLink doc={doc as string} page={null} />
                        </li>
                      ))}
                    {card.roll.map((r) => (
                      <li key={r.revision}>
                        <SourceLink doc={r.source_doc} page={r.source_page} />
                      </li>
                    ))}
                    {card.results.length === 0 && card.roll.length === 0 && (
                      <li><Missing reason={t('prov.noSource')} /></li>
                    )}
                  </ul>
                </section>

                <section className="card px-3 py-2">
                  <strong className="text-sm">{t('card.crosswalk')}</strong>
                  {card.crosswalk.length === 0 ? (
                    <p className="mt-1 text-2xs">
                      <Missing reason={t('booths.noCrosswalk')} />
                    </p>
                  ) : (
                    <ul className="mt-1 space-y-0.5 text-2xs">
                      {card.crosswalk.map((x) => {
                        const weak = !x.reviewed && (x.confidence ?? 0) < 0.85
                        return (
                          <li key={x.election_label}
                              style={{ color: weak ? 'var(--status-warn, var(--text-muted))' : undefined }}>
                            {x.election_label}: PS {x.ps_number} ·{' '}
                            {x.confidence?.toFixed(2) ?? '—'} · {x.match_method}
                            {weak && ` · ${t('booths.weakCrosswalk')}`}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>

                <section className="card px-3 py-2">
                  <strong className="text-sm">{t('card.priority')}</strong>
                  <dl className="mt-1 grid grid-cols-2 gap-x-2 text-2xs">
                    <dt style={{ color: 'var(--text-muted)' }}>{t('card.score')}</dt>
                    <dd className="tnum text-right">
                      <Value value={card.priority.priority_score}
                             reason={t('card.noPriority')}
                             render={(v) => v.toFixed(3)} />
                    </dd>
                    <dt style={{ color: 'var(--text-muted)' }}>{t('card.inputsUsed')}</dt>
                    <dd className="text-right text-3xs">
                      {card.priority.inputs_used.length
                        ? card.priority.inputs_used.join(', ')
                        : <Missing reason={t('card.noPriority')} />}
                    </dd>
                  </dl>
                  {/* The weight actually used, because a score renormalised over
                      two of four inputs is not the four-factor score it looks
                      like. */}
                  {card.priority.weight_used !== null && card.priority.weight_used < 1 && (
                    <p className="mt-1 text-3xs"
                       style={{ color: 'var(--status-warn, var(--text-muted))' }}>
                      {t('card.partialWeight', {
                        pct: Math.round(card.priority.weight_used * 100),
                      })}
                    </p>
                  )}
                </section>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
