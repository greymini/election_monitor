import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import PartyChip from './PartyChip'
import { ErrorState, Loading } from './States'
import { api } from '../lib/api'
import { CONFIDENCE_FLOOR, num, pct, signedNum } from '../lib/format'

interface BoothCard {
  booth: Record<string, string | number | null>
  results: Array<Record<string, number | string | null>>
  roll_snapshots: Array<Record<string, number | string | null>>
  roll_changes: Array<Record<string, number | string | null>>
  new_voters: Record<string, number> | null
  priority: Record<string, number | string> | null
  crosswalk: Array<{
    label: string; ps_number: number; match_method: string
    confidence: number; reviewed: boolean
  }>
  caste_estimate?: Array<{
    name_en: string; name_hi: string; est_pct: number; confidence: number; source: string
  }>
  caste_note?: string
  caveats: string[]
}

export default function BoothDrawer({ boothUid, onClose }: { boothUid: string; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const query = useQuery<BoothCard>({
    queryKey: ['booth-card', boothUid],
    queryFn: () => api.get(`/booths/${boothUid}/card`),
  })
  const card = query.data

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <button className="flex-1 bg-black/25" aria-label={t('common.close')} onClick={onClose} />
      <div className="w-full max-w-md overflow-auto p-3" style={{ background: 'var(--plane)' }}>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold">{boothUid}</h2>
          <button className="btn px-2 py-1 text-2xs" onClick={onClose}>{t('common.close')}</button>
        </div>

        {query.isLoading && <Loading />}
        {query.isError && <ErrorState error={query.error} />}

        {card && (
          <div className="space-y-3">
            <section className="card px-3 py-2">
              <div className="text-sm font-medium">{card.booth.building ?? '—'}</div>
              <div className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
                {card.booth.village_or_locality ?? '—'} ·{' '}
                {hi ? card.booth.area_hi : card.booth.area_en} ·{' '}
                {hi ? card.booth.block_hi : card.booth.block_en}
              </div>
            </section>

            {card.caveats.length > 0 && (
              <section className="card px-3 py-2">
                {card.caveats.map((caveat, i) => (
                  <p key={i} className="text-2xs" style={{ color: 'var(--status-serious)' }}>
                    ! {caveat}
                  </p>
                ))}
              </section>
            )}

            <section className="card px-3 py-2">
              <h3 className="text-2xs font-semibold uppercase tracking-wide"
                  style={{ color: 'var(--text-muted)' }}>
                {t('common.election')}
              </h3>
              <table className="mt-1 w-full border-collapse text-2xs">
                <thead>
                  <tr>
                    <th className="th">{t('common.election')}</th>
                    <th className="th" style={{ textAlign: 'right' }}>{t('common.winner')}</th>
                    <th className="th" style={{ textAlign: 'right' }}>{t('common.margin')}</th>
                    <th className="th" style={{ textAlign: 'right' }}>{t('common.turnout')}</th>
                  </tr>
                </thead>
                <tbody>
                  {card.results.map((row, i) => (
                    <tr key={i}>
                      <td className="td">{String(row.election_label)}</td>
                      <td className="td" style={{ textAlign: 'right' }}>
                        <PartyChip abbr={row.winner_party as string} />
                      </td>
                      <td className="td tnum" style={{ textAlign: 'right' }}>
                        {num(row.margin_votes as number)} ({pct(row.margin_pct as number)})
                      </td>
                      <td className="td tnum" style={{ textAlign: 'right' }}>
                        {pct(row.turnout_pct as number)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {card.results[0]?.source_doc && (
                <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
                  {t('common.source')}: {String(card.results[0].source_doc)} p.
                  {String(card.results[0].source_page ?? '?')}
                </p>
              )}
            </section>

            {card.new_voters && (
              <section className="card px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-wide"
                    style={{ color: 'var(--text-muted)' }}>
                  {t('voters.heading')}
                </h3>
                <div className="tnum mt-1 grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {t('voters.additions')}
                    </div>
                    {num(card.new_voters.additions)}
                  </div>
                  <div>
                    <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {t('voters.deletions')}
                    </div>
                    {num(card.new_voters.deletions)}
                  </div>
                  <div>
                    <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {t('voters.net')}
                    </div>
                    {signedNum(card.new_voters.net_change)}
                  </div>
                </div>
                <p className="mt-1 text-2xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('voters.newVoterShare')}: {pct(card.new_voters.new_voter_pct)} of{' '}
                  {num(card.new_voters.electors_now)} electors
                </p>
              </section>
            )}

            {card.caste_estimate && card.caste_estimate.length > 0 && (
              <section className="card px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-wide"
                    style={{ color: 'var(--text-muted)' }}>
                  {t('caste.heading')}
                </h3>
                <ul className="mt-1 space-y-0.5">
                  {card.caste_estimate.map((row) => {
                    const weak = row.confidence < CONFIDENCE_FLOOR
                    const ink = weak ? 'var(--text-muted)' : 'var(--text-primary)'
                    return (
                      <li key={row.name_en} className="flex items-center justify-between text-2xs">
                        <span style={{ color: ink }}>{hi ? row.name_hi : row.name_en}</span>
                        <span className="tnum" style={{ color: ink }}>
                          {weak ? t('caste.insufficient') : pct(row.est_pct)}
                          <span className="ml-1" style={{ color: 'var(--text-muted)' }}>
                            ({row.confidence.toFixed(2)})
                          </span>
                        </span>
                      </li>
                    )
                  })}
                </ul>
                <p className="mt-1.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                  {card.caste_note}
                </p>
              </section>
            )}

            <section className="card px-3 py-2">
              <h3 className="text-2xs font-semibold uppercase tracking-wide"
                  style={{ color: 'var(--text-muted)' }}>
                Cross-year matching
              </h3>
              <ul className="mt-1 space-y-0.5 text-2xs">
                {card.crosswalk.map((row) => {
                  const weak = row.confidence < 0.85 && !row.reviewed
                  return (
                    <li key={row.label} className="flex justify-between">
                      <span>{row.label} · PS {row.ps_number}</span>
                      <span className="tnum"
                            style={{ color: weak ? 'var(--status-serious)' : 'var(--text-secondary)' }}>
                        {row.match_method} {row.confidence.toFixed(2)}{row.reviewed ? ' ✓' : ''}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </section>

            {card.roll_changes.length > 0 && (
              <section className="card px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-wide"
                    style={{ color: 'var(--text-muted)' }}>
                  Roll revisions
                </h3>
                <ul className="mt-1 space-y-0.5 text-2xs">
                  {card.roll_changes.map((row, i) => (
                    <li key={i} className="flex justify-between">
                      <span>
                        {String(row.label)}
                        {row.is_post_sir ? <span className="chip ml-1">SIR</span> : null}
                      </span>
                      <span className="tnum">
                        +{num(row.additions as number)} / −{num(row.deletions as number)}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
