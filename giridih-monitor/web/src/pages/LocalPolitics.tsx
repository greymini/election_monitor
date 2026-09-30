import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { FixtureBanner, Missing } from '../components/Provenance'
import { Empty, ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { dateShort } from '../lib/format'
import { partyColor } from '../lib/tokens'

/**
 * Local politics (spec §7.10): office holders, organisations, event timeline.
 *
 * The thing this page has to be careful about is the party tag. Panchayat
 * elections in Jharkhand are contested without party symbols, so any party
 * shown against a mukhiya is somebody's judgement, not a published fact. The
 * HLD lists that subjectivity as a named risk and requires the source of the
 * tag to be recorded; this page shows the tag and its source together, and an
 * untagged holder reads as "untagged" rather than as independent - those are
 * different claims.
 *
 * The influencer registry is deliberately absent here. It holds named
 * individuals and is restricted to strategist and admin; it arrives with the
 * role-gated endpoints in Track B rather than being half-built now.
 */

interface OfficeHolder {
  id: number
  office: string
  name: string
  area_en: string
  tagged_party: string | null
  tag_source: string | null
  term_start: string | null
  term_end: string | null
}

interface Event {
  id: number
  occurred_on: string
  kind: string
  area_en: string | null
  title: string
  effect_party: string | null
  effect_sign: number | null
  source: string | null
}

interface Organisation {
  id: number
  name: string
  kind: string
  community: string | null
  alignment_party: string | null
}

interface Response {
  office_holders: OfficeHolder[]
  events: Event[]
  organisations: Organisation[]
  note: string
  fixture?: string | null
}

export default function LocalPolitics({ ac }: { ac: AcState }) {
  const { t } = useTranslation()

  const query = useQuery<Response>({
    queryKey: ['local-politics', ac.acNumber],
    queryFn: () => api.get(ac.path('/local-politics')),
    enabled: ac.acNumber !== null,
  })

  if (ac.acNumber === null || query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const data = query.data
  const empty = !data
    || (data.office_holders.length === 0 && data.events.length === 0
      && data.organisations.length === 0)

  return (
    <div className="space-y-3">
      <FixtureBanner note={data?.fixture} />
      <h1 className="text-lg font-semibold">{t('localPolitics.heading')}</h1>

      {empty ? (
        <Empty hint="No local political data is loaded. Run: python -m ingest.load_csv local_office_holder <csv>" />
      ) : (
        <>
          <section className="card overflow-x-auto px-0 py-0">
            <h2 className="px-3 pt-3 text-sm font-semibold">{t('localPolitics.holders')}</h2>
            <table className="mt-1 w-full text-sm">
              <thead>
                <tr className="text-left text-2xs" style={{ color: 'var(--text-muted)' }}>
                  <th className="px-3 py-2">{t('localPolitics.office')}</th>
                  <th className="px-3 py-2">{t('localPolitics.name')}</th>
                  <th className="px-3 py-2">{t('common.area')}</th>
                  <th className="px-3 py-2">{t('localPolitics.tag')}</th>
                  <th className="px-3 py-2">{t('localPolitics.tagSource')}</th>
                </tr>
              </thead>
              <tbody>
                {data!.office_holders.map((h) => (
                  <tr key={h.id} className="border-t" style={{ borderColor: 'var(--surface-2)' }}>
                    <td className="px-3 py-1.5 text-2xs">{h.office}</td>
                    <td className="px-3 py-1.5">{h.name}</td>
                    <td className="px-3 py-1.5">{h.area_en}</td>
                    <td className="px-3 py-1.5">
                      {h.tagged_party
                        ? (
                          <span style={{ color: partyColor(h.tagged_party) }}>
                            {h.tagged_party}
                          </span>
                        )
                        : (
                          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}
                                title={t('localPolitics.untaggedHelp')}>
                            {t('localPolitics.untagged')}
                          </span>
                        )}
                    </td>
                    <td className="px-3 py-1.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {h.tag_source ?? <Missing reason={t('localPolitics.noTagSource')} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="px-3 pb-3 pt-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
              {t('localPolitics.tagCaveat')}
            </p>
          </section>

          {data!.events.length > 0 && (
            <section className="card px-4 py-3">
              <h2 className="text-sm font-semibold">{t('localPolitics.events')}</h2>
              <ol className="mt-2 space-y-2">
                {data!.events.map((e) => (
                  <li key={e.id} className="flex gap-2">
                    <div className="w-20 shrink-0 text-2xs tnum"
                         style={{ color: 'var(--text-muted)' }}>
                      {dateShort(e.occurred_on)}
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm">{e.title}</div>
                      <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                        {e.kind}
                        {e.area_en ? ` · ${e.area_en}` : ''}
                        {e.effect_party && (
                          <>
                            {' · '}
                            <span style={{ color: partyColor(e.effect_party) }}>
                              {e.effect_party}
                            </span>
                            {e.effect_sign
                              ? ` ${e.effect_sign > 0 ? '+' : '−'}`
                              : ''}
                          </>
                        )}
                        {e.source ? ` · ${e.source}` : ''}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
              <p className="mt-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
                {t('localPolitics.effectCaveat')}
              </p>
            </section>
          )}

          {data!.organisations.length > 0 && (
            <section className="card px-4 py-3">
              <h2 className="text-sm font-semibold">{t('localPolitics.organisations')}</h2>
              <ul className="mt-1.5 space-y-1 text-sm">
                {data!.organisations.map((o) => (
                  <li key={o.id}>
                    {o.name}
                    <span className="ml-1.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {o.kind}
                      {o.community ? ` · ${o.community}` : ''}
                      {o.alignment_party
                        ? ` · ${o.alignment_party}`
                        : ` · ${t('localPolitics.untagged')}`}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {data?.note}
      </p>
    </div>
  )
}
