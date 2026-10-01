import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import PartyChip from '../components/PartyChip'
import { FixtureBanner, Value } from '../components/Provenance'
import { Empty, ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'

/**
 * Candidate profiles (spec §7.9), side by side for one contest.
 *
 * The turncoat and incumbent badges are the point of the page. The spec's
 * reasoning: party-switching and re-runs are the story in every one of these six
 * seats, and a table of names and vote counts hides exactly that.
 *
 * Everything here is declared or transcribed rather than counted, so every field
 * says so. Assets and criminal cases come from the candidate's own affidavit -
 * the honest label is "as declared", not "assets" - and the whole set is seeded
 * unverified until somebody checks it against the affidavit PDF.
 */

interface Candidate {
  candidate_id: number
  name_en: string
  name_hi: string | null
  party: string | null
  election_label: string
  votes: number | null
  share_pct: number | null
  is_winner: boolean
  incumbent: boolean | null
  contests_prior: number | null
  wins_prior: number | null
  prev_party: string | null
  turncoat: boolean | null
  deposit_forfeited: boolean | null
  age: number | null
  education: string | null
  profession: string | null
  assets_declared: number | null
  liabilities: number | null
  criminal_cases: number | null
  criminal_serious: number | null
  source: string | null
  affidavit_url: string | null
}

interface Response {
  rows: Candidate[]
  note: string
  fixture?: string | null
}

/** Indian-grouped rupees from a paise-free integer. */
function rupees(value: number): string {
  if (value >= 10000000) return `₹${(value / 10000000).toFixed(2)} Cr`
  if (value >= 100000) return `₹${(value / 100000).toFixed(2)} L`
  return `₹${num(value)}`
}

export default function Candidates({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'

  const query = useQuery<Response>({
    queryKey: ['candidates', ac.acNumber],
    queryFn: () => api.get(ac.path('/candidates')),
    enabled: ac.acNumber !== null,
  })

  if (ac.acNumber === null || query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const rows = query.data?.rows ?? []
  // One section per election, newest first, in the order the API ranks them
  // (votes, descending): fourteen candidates stood in 2024 and a flat grid
  // mixed them with 2019's twelve.
  const groups = rows.reduce<Array<{ label: string; rows: Candidate[] }>>((out, row) => {
    const last = out[out.length - 1]
    if (last && last.label === row.election_label) last.rows.push(row)
    else out.push({ label: row.election_label, rows: [row] })
    return out
  }, [])

  return (
    <div className="space-y-3">
      <FixtureBanner note={query.data?.fixture} />
      <h1 className="text-lg font-semibold">{t('candidates.heading')}</h1>

      {rows.length === 0 ? (
        <Empty hint={`No candidate profiles are loaded. Run: python -m ingest.load_csv candidate_profile <csv> --ac ${ac.acNumber}`} />
      ) : (
        groups.map((group) => (
        <section key={group.label} className="space-y-2" data-testid={`candidates-${group.label}`}>
        <h2 className="text-sm font-semibold">
          {t('candidates.electionHeading', { election: group.label, count: group.rows.length })}
        </h2>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {group.rows.map((c) => (
            <article key={c.candidate_id} className="card px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold">{hi && c.name_hi ? c.name_hi : c.name_en}</h2>
                  <p className="text-2xs">
                    <PartyChip abbr={c.party ?? 'UNK'} />
                    <span style={{ color: 'var(--text-muted)' }}> · {c.election_label}</span>
                  </p>
                </div>
                {c.is_winner && (
                  <span className="rounded px-1.5 py-0.5 text-3xs"
                        style={{ background: 'var(--surface-2)' }}>
                    {t('common.winner')}
                  </span>
                )}
              </div>

              {/* The badges that carry the story. */}
              <div className="mt-1.5 flex flex-wrap gap-1">
                {c.incumbent && (
                  <span className="rounded px-1.5 py-0.5 text-3xs"
                        style={{ background: 'var(--surface-2)' }}>
                    {t('candidates.incumbent')}
                  </span>
                )}
                {c.turncoat && (
                  <span className="rounded px-1.5 py-0.5 text-3xs"
                        style={{
                          background: 'var(--status-warn-bg, var(--surface-2))',
                          color: 'var(--status-warn, var(--text-secondary))',
                        }}
                        title={c.prev_party
                          ? t('candidates.turncoatFrom', { party: c.prev_party })
                          : undefined}>
                    {t('candidates.turncoat')}
                    {c.prev_party ? ` (${c.prev_party})` : ''}
                  </span>
                )}
                {c.deposit_forfeited && (
                  <span className="rounded px-1.5 py-0.5 text-3xs"
                        style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
                    {t('candidates.forfeited')}
                  </span>
                )}
              </div>

              <dl className="mt-2 grid grid-cols-2 gap-x-2 gap-y-0.5 text-2xs">
                <dt style={{ color: 'var(--text-muted)' }}>{t('common.votes')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.votes} reason={t('overview.noResultsYet')}
                         render={(v) => num(v)} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('results.share')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.share_pct} reason={t('overview.noResultsYet')}
                         render={(v) => pct(v)} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('candidates.contests')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.contests_prior} reason={t('candidates.notDeclared')}
                         render={(v) => `${num(v)} (${num(c.wins_prior ?? 0)} won)`} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('candidates.age')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.age} reason={t('candidates.notDeclared')}
                         render={(v) => num(v)} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('candidates.education')}</dt>
                <dd className="text-right">
                  <Value value={c.education} reason={t('candidates.notDeclared')}
                         render={(v) => v} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('candidates.assets')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.assets_declared} reason={t('candidates.notDeclared')}
                         render={(v) => rupees(v)} />
                </dd>
                <dt style={{ color: 'var(--text-muted)' }}>{t('candidates.cases')}</dt>
                <dd className="tnum text-right">
                  <Value value={c.criminal_cases} reason={t('candidates.notDeclared')}
                         render={(v) => v === 0
                           ? t('candidates.noneDeclared')
                           : `${num(v)}${c.criminal_serious ? ` (${c.criminal_serious} serious)` : ''}`} />
                </dd>
              </dl>

              <p className="mt-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
                {t('candidates.declared')}
                {c.source ? ` · ${c.source}` : ''}
                {c.affidavit_url && (
                  <>
                    {' · '}
                    <a className="underline decoration-dotted" href={c.affidavit_url}
                       target="_blank" rel="noreferrer">
                      {t('candidates.affidavit')}
                    </a>
                  </>
                )}
              </p>
            </article>
          ))}
        </div>
        </section>
        ))
      )}

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {query.data?.note}
      </p>
    </div>
  )
}
