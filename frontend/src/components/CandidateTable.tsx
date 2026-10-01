import { useTranslation } from 'react-i18next'

import PartyChip from './PartyChip'
import { ErrorState, Loading } from './States'
import type { AcState } from '../lib/ac'
import { num, pct } from '../lib/format'
import { useElectionCandidates, type ElectionCandidates } from '../lib/results'

/**
 * Every candidate in one election, ranked: the declared result, not the
 * JMM/BJP/JLKM pivot. Form 20 prints eleven of the fourteen 2024 candidates
 * with no party, and before the real load they existed only as "others".
 */
export default function CandidateTable({ ac, election }: { ac: AcState; election: string }) {
  const { t } = useTranslation()
  const query = useElectionCandidates(ac, election)
  if (query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return null
  return <CandidateTableView data={query.data} t={t} />
}

export function CandidateTableView({ data, t }: {
  data: ElectionCandidates
  t: (key: string, opts?: Record<string, unknown>) => string
}) {
  const form20 = data.basis === 'form20'
  const doc = data.sources[0]
  return (
    <section className="card overflow-x-auto px-0 py-0" data-testid="candidate-table">
      <div className="px-3 pt-3">
        <h2 className="text-sm font-semibold">
          {t('results.candidatesHeading', { election: data.election.label })}
        </h2>
        <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
          {form20
            ? t('results.candidatesForm20', {
              doc: doc?.source_doc ?? '—',
              sha: doc?.sha256 ? doc.sha256.slice(0, 12) : '—',
            })
            : t('results.candidatesPublished')}
        </p>
      </div>
      <table className="mt-2 w-full text-sm">
        <thead>
          <tr className="text-left text-2xs" style={{ color: 'var(--text-muted)' }}>
            <th className="px-3 py-1.5 text-right">#</th>
            <th className="px-3 py-1.5">{t('results.candidate')}</th>
            <th className="px-3 py-1.5">{t('common.party')}</th>
            {form20 && <th className="px-3 py-1.5 text-right">{t('results.evm')}</th>}
            {form20 && <th className="px-3 py-1.5 text-right">{t('results.postal')}</th>}
            <th className="px-3 py-1.5 text-right">{t('common.votes')}</th>
            <th className="px-3 py-1.5 text-right">{t('results.share')}</th>
            {form20 && <th className="px-3 py-1.5 text-right">{t('results.boothsLed')}</th>}
            <th className="px-3 py-1.5">{t('results.deposit')}</th>
          </tr>
        </thead>
        <tbody>
          {data.candidates.map((c) => (
            <tr key={c.candidate_id} className="border-t" style={{ borderColor: 'var(--gridline)' }}>
              <td className="px-3 py-1.5 text-right tabular-nums">{c.rank}</td>
              <td className="px-3 py-1.5">
                <span className={c.is_winner ? 'font-semibold' : undefined}>{c.candidate}</span>
                {c.is_winner && (
                  <span className="ml-1.5 rounded px-1 text-3xs"
                        style={{ background: 'var(--surface-2)' }}>
                    {t('results.elected')}
                  </span>
                )}
              </td>
              <td className="px-3 py-1.5"><PartyChip abbr={c.party} /></td>
              {form20 && <td className="px-3 py-1.5 text-right tabular-nums">{num(c.evm_votes)}</td>}
              {form20 && <td className="px-3 py-1.5 text-right tabular-nums">{num(c.postal_votes)}</td>}
              <td className="px-3 py-1.5 text-right tabular-nums">{num(c.votes)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{pct(c.share_pct, 2)}</td>
              {form20 && <td className="px-3 py-1.5 text-right tabular-nums">{num(c.booths_led)}</td>}
              <td className="px-3 py-1.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                {c.deposit_forfeited ? t('candidates.forfeited') : ''}
              </td>
            </tr>
          ))}
          {data.nota && (
            <tr className="border-t" style={{ borderColor: 'var(--gridline)', color: 'var(--text-muted)' }}>
              <td />
              <td className="px-3 py-1.5">NOTA</td>
              <td />
              {form20 && <td className="px-3 py-1.5 text-right tabular-nums">{num(data.nota.evm_votes)}</td>}
              {form20 && <td className="px-3 py-1.5 text-right tabular-nums">{num(data.nota.postal_votes)}</td>}
              <td className="px-3 py-1.5 text-right tabular-nums">{num(data.nota.votes)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{pct(data.nota.share_pct, 2)}</td>
              {form20 && <td />}
              <td />
            </tr>
          )}
          <tr className="border-t text-2xs" style={{ borderColor: 'var(--gridline)' }}>
            <td />
            <td className="px-3 py-1.5" colSpan={form20 ? 4 : 2}>{t('results.validTotal')}</td>
            <td className="px-3 py-1.5 text-right tabular-nums">{num(data.valid_votes)}</td>
            <td colSpan={form20 ? 3 : 2} />
          </tr>
        </tbody>
      </table>
      <ul className="space-y-0.5 px-3 py-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
        {data.notes.map((note) => <li key={note}>{note}</li>)}
        <li>{data.deposit_rule}</li>
      </ul>
    </section>
  )
}
