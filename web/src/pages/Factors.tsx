import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import type { AcState } from '../lib/ac'

/**
 * Curated knowledge cards (HLD module 9), read from the database.
 *
 * These used to be a hardcoded array in this file, with a comment claiming they
 * were "the same cards that go into the assistant's cached prompt, so what the
 * dashboard shows and what the assistant knows cannot drift apart". They were
 * not: the assistant reads the `knowledge_card` table, this page read a
 * constant, and the two had already drifted - five cards here against six
 * seeded, and a slug that did not match. The array also carried LS and VS vote
 * counts and the 2024 margin as literals, which nothing checked against the
 * loaded results, so once a Form 20 was loaded the page could contradict every
 * other screen and no test would notice.
 *
 * One source of truth now. Edit the cards in db/seed/knowledge_cards/ and load
 * them with `python -m db.seed.load_seed`.
 */

interface Card {
  slug: string
  topic: string
  title_en: string | null
  title_hi: string | null
  body_en: string | null
  body_hi: string | null
  sources: string[] | null
  last_reviewed: string | null
  in_prompt: boolean
}

interface CardsResponse {
  cards: Card[]
  note: string
}

export default function Factors({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const query = useQuery<CardsResponse>({
    queryKey: ['knowledge-cards', ac.acNumber],
    queryFn: () => api.get(ac.path('/knowledge-cards')),
    enabled: ac.acNumber !== null,
  })

  if (query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const cards = query.data?.cards ?? []

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">{t('nav.factors')}</h1>
      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('factors.intro')}
      </p>

      {cards.length === 0 ? (
        <Empty hint={`${t('factors.empty')} Run: python -m db.seed.load_seed`} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {cards.map((card) => (
            <article key={card.slug} className="card px-4 py-3">
              <h2 className="text-sm font-semibold">
                {(hi ? card.title_hi : card.title_en) ?? card.topic}
              </h2>
              <p
                className="mt-1.5 whitespace-pre-line text-sm leading-relaxed"
                style={{ color: 'var(--text-secondary)' }}
              >
                {(hi ? card.body_hi : card.body_en) ?? ''}
              </p>
              <div
                className="mt-2 flex flex-wrap items-baseline gap-x-3 text-2xs"
                style={{ color: 'var(--text-muted)' }}
              >
                {card.sources && card.sources.length > 0 && (
                  <span>
                    {t('common.source')}: {card.sources.join(', ')}
                  </span>
                )}
                {card.last_reviewed && (
                  <span>
                    {t('factors.reviewed')}: {card.last_reviewed}
                  </span>
                )}
                {!card.in_prompt && <span>{t('factors.notInPrompt')}</span>}
              </div>
            </article>
          ))}
        </div>
      )}

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {query.data?.note}
      </p>
    </div>
  )
}
