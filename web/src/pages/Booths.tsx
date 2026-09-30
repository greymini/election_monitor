import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import BoothDrawer from '../components/BoothDrawer'
import { FixtureBanner, Missing, SourceLink } from '../components/Provenance'
import { Empty, ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api, downloadCsv } from '../lib/api'
import { num, pct } from '../lib/format'
import { partyColor } from '../lib/tokens'

/**
 * The booth table (spec §7.2), which the spec calls the workhorse and says
 * replaces half the other modules.
 *
 * One row per booth, every column available, column presets, per-column
 * filters, multi-sort, sticky header and first column, CSV of the current view,
 * and a source-page link per row.
 *
 * The rule that shapes every cell: a NULL renders as an em dash with the reason
 * it is absent, never as 0 and never as blank. This table is where that matters
 * most, because it is the one place a reader scans hundreds of numbers quickly -
 * and it carries five different kinds of absence, each with its own cause:
 *
 *   - electors and turnout, where no roll snapshot is linked (B4)
 *   - swing, where there is no prior election, or the crosswalk is weak and
 *     unreviewed, or the booth split (D2, B2)
 *   - new-voter share, where a roll revision is missing at either end (B1)
 *   - floating vote, where only one poll type is loaded (D4)
 *   - volatility, where fewer than two years exist
 *
 * Each of those was, in the audited system, a zero or a constant that read as
 * data.
 */

interface Row {
  booth_uid: string
  ps_numbers: string
  area_en: string
  area_hi: string
  block_en: string
  building: string
  electors: number | null
  valid_votes: number
  votes_polled: number
  rejected: number
  nota: number
  jmm: number
  bjp: number
  jlkm: number
  others: number
  winner_party: string | null
  runner_party: string | null
  margin_votes: number | null
  margin_pct: number | null
  signed_margin_pct: number | null
  turnout_pct: number | null
  turnout_null_reason: string | null
  jmm_swing_pct: number | null
  swing_null_reason: string | null
  new_voter_pct: number | null
  new_voter_null_reason: string | null
  additions: number | null
  floating_pct: number | null
  floating_null_reason: string | null
  margin_stddev: number | null
  volatility_null_reason: string | null
  crosswalk_confidence: number | null
  crosswalk_reviewed: boolean
  lineage_kind: string | null
  source_doc: string | null
  source_page: number | null
}

interface Response {
  election_label: string
  ac_number: number
  rows: Row[]
  count: number
  fixture?: string | null
}

type Align = 'left' | 'right'

interface Column {
  key: string
  label: string
  align: Align
  /** Sort value. NULL sorts last in either direction, always. */
  sort?: (r: Row) => number | string | null
  render: (r: Row, helpers: Helpers) => React.ReactNode
  /** Free-text filter target. */
  filter?: (r: Row) => string
}

interface Helpers {
  t: (k: string, o?: Record<string, unknown>) => string
  hi: boolean
}

/** Column presets (spec §7.2). */
const PRESETS: Record<string, string[]> = {
  results: ['booth_uid', 'area', 'block', 'electors', 'turnout', 'jmm', 'bjp',
    'jlkm', 'nota', 'winner', 'margin', 'source'],
  swing: ['booth_uid', 'area', 'jmm_swing', 'signed_margin', 'crosswalk', 'source'],
  voters: ['booth_uid', 'area', 'electors', 'additions', 'new_voter', 'turnout'],
  community: ['booth_uid', 'area', 'electors', 'winner', 'margin'],
  priority: ['booth_uid', 'area', 'margin', 'new_voter', 'floating', 'volatility'],
}

export default function Booths({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [preset, setPreset] = useState<keyof typeof PRESETS>('results')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [sorts, setSorts] = useState<Array<{ key: string; dir: 1 | -1 }>>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [election, setElection] = useState('VS-2024')

  const query = useQuery<Response>({
    queryKey: ['booth-table', ac.acNumber, election],
    queryFn: () => api.get(ac.path(`/results/${encodeURIComponent(election)}/booths`)),
    enabled: ac.acNumber !== null,
  })

  const columns: Column[] = useMemo(() => [
    {
      key: 'booth_uid', label: t('common.booth'), align: 'left',
      sort: (r) => r.booth_uid,
      filter: (r) => `${r.booth_uid} ${r.ps_numbers} ${r.building}`,
      render: (r) => (
        <button
          className="text-left underline decoration-dotted"
          onClick={() => setSelected(r.booth_uid)}
          title={r.building}
        >
          {r.booth_uid}
          <span className="ml-1 text-3xs" style={{ color: 'var(--text-muted)' }}>
            PS {r.ps_numbers}
          </span>
        </button>
      ),
    },
    {
      key: 'area', label: t('common.area'), align: 'left',
      sort: (r) => r.area_en, filter: (r) => `${r.area_en} ${r.area_hi}`,
      render: (r) => (hi ? r.area_hi : r.area_en),
    },
    {
      key: 'block', label: t('common.block'), align: 'left',
      sort: (r) => r.block_en, filter: (r) => r.block_en,
      render: (r) => r.block_en,
    },
    {
      key: 'electors', label: t('common.electors'), align: 'right',
      sort: (r) => r.electors,
      render: (r, h) => r.electors === null
        ? <Missing reason={r.turnout_null_reason ?? h.t('overview.noRollLinked')} />
        : num(r.electors),
    },
    {
      key: 'turnout', label: t('common.turnout'), align: 'right',
      sort: (r) => r.turnout_pct,
      render: (r, h) => r.turnout_pct === null
        ? <Missing reason={r.turnout_null_reason ?? h.t('overview.noRollLinked')} />
        : pct(r.turnout_pct),
    },
    ...(['jmm', 'bjp', 'jlkm', 'others', 'nota'] as const).map((party): Column => ({
      key: party,
      label: party === 'others' ? t('common.party') + ' +' : party.toUpperCase(),
      align: 'right',
      sort: (r) => r[party],
      render: (r) => (
        <span title={`${pct(Math.round((1000 * r[party]) / r.valid_votes) / 10)}`}>
          {num(r[party])}
        </span>
      ),
    })),
    {
      key: 'winner', label: t('common.winner'), align: 'left',
      sort: (r) => r.winner_party,
      filter: (r) => `${r.winner_party ?? ''} ${r.runner_party ?? ''}`,
      render: (r) => r.winner_party === null
        ? <Missing reason={t('overview.noResultsYet')} />
        : (
          <span>
            <span style={{ color: partyColor(r.winner_party) }}>{r.winner_party}</span>
            <span style={{ color: 'var(--text-muted)' }}> / {r.runner_party ?? '—'}</span>
          </span>
        ),
    },
    {
      key: 'margin', label: t('common.margin'), align: 'right',
      sort: (r) => r.margin_pct,
      render: (r) => r.margin_votes === null || r.margin_pct === null
        ? <Missing reason={t('booths.noMargin')} />
        : (
          <span title={`${num(r.margin_votes)} votes`}>{pct(r.margin_pct)}</span>
        ),
    },
    {
      key: 'signed_margin', label: t('booths.signedMargin'), align: 'right',
      sort: (r) => r.signed_margin_pct,
      render: (r) => r.signed_margin_pct === null
        ? <Missing reason={t('booths.noSignedMargin')} />
        : (
          <span style={{ color: partyColor(r.signed_margin_pct > 0 ? 'JMM' : 'BJP') }}>
            {r.signed_margin_pct > 0 ? '+' : ''}{pct(r.signed_margin_pct)}
          </span>
        ),
    },
    {
      key: 'jmm_swing', label: t('booths.swing'), align: 'right',
      sort: (r) => r.jmm_swing_pct,
      render: (r) => r.jmm_swing_pct === null
        ? <Missing reason={r.swing_null_reason ?? t('booths.noSwing')} />
        : `${r.jmm_swing_pct > 0 ? '+' : ''}${pct(r.jmm_swing_pct)}`,
    },
    {
      key: 'additions', label: t('voters.additions'), align: 'right',
      sort: (r) => r.additions,
      render: (r) => r.additions === null
        ? <Missing reason={r.new_voter_null_reason ?? t('booths.noRoll')} />
        : num(r.additions),
    },
    {
      key: 'new_voter', label: t('booths.newVoterShare'), align: 'right',
      sort: (r) => r.new_voter_pct,
      render: (r) => r.new_voter_pct === null
        ? <Missing reason={r.new_voter_null_reason ?? t('booths.noRoll')} />
        : pct(r.new_voter_pct),
    },
    {
      key: 'floating', label: t('booths.floating'), align: 'right',
      sort: (r) => r.floating_pct,
      render: (r) => r.floating_pct === null
        ? <Missing reason={r.floating_null_reason ?? t('booths.noFloating')} />
        : pct(r.floating_pct),
    },
    {
      key: 'volatility', label: t('booths.volatility'), align: 'right',
      sort: (r) => r.margin_stddev,
      render: (r) => r.margin_stddev === null
        ? <Missing reason={r.volatility_null_reason ?? t('booths.noVolatility')} />
        : pct(r.margin_stddev),
    },
    {
      key: 'crosswalk', label: t('booths.crosswalk'), align: 'right',
      sort: (r) => r.crosswalk_confidence,
      render: (r) => {
        if (r.crosswalk_confidence === null) {
          return <Missing reason={t('booths.noCrosswalk')} />
        }
        const weak = !r.crosswalk_reviewed && r.crosswalk_confidence < 0.85
        return (
          <span
            style={{ color: weak ? 'var(--status-warn, var(--text-muted))' : undefined }}
            title={weak ? t('booths.weakCrosswalk') : t('booths.goodCrosswalk')}
          >
            {r.crosswalk_confidence.toFixed(2)}
            {r.lineage_kind && (
              <span className="ml-1 text-3xs">{r.lineage_kind}</span>
            )}
          </span>
        )
      },
    },
    {
      key: 'source', label: t('common.source'), align: 'left',
      render: (r) => <SourceLink doc={r.source_doc} page={r.source_page} compact />,
    },
  ], [t, hi])

  const visible = columns.filter((c) => PRESETS[preset].includes(c.key))

  const rows = useMemo(() => {
    let out = query.data?.rows ?? []
    for (const [key, term] of Object.entries(filters)) {
      if (!term.trim()) continue
      const col = columns.find((c) => c.key === key)
      if (!col?.filter) continue
      const needle = term.toLowerCase()
      out = out.filter((r) => col.filter!(r).toLowerCase().includes(needle))
    }
    if (sorts.length) {
      out = [...out].sort((a, b) => {
        for (const { key, dir } of sorts) {
          const col = columns.find((c) => c.key === key)
          if (!col?.sort) continue
          const va = col.sort(a)
          const vb = col.sort(b)
          // A NULL sorts last whichever way the column is sorted: an absent
          // figure is not a small one, and letting it head an ascending sort
          // would put "unknown" at the top of a tightest-margin list.
          if (va === null && vb === null) continue
          if (va === null) return 1
          if (vb === null) return -1
          if (va < vb) return -1 * dir
          if (va > vb) return 1 * dir
        }
        return a.booth_uid.localeCompare(b.booth_uid)
      })
    }
    return out
  }, [query.data, filters, sorts, columns])

  const toggleSort = (key: string, additive: boolean) => {
    setSorts((prev) => {
      const existing = prev.find((s) => s.key === key)
      const next: Array<{ key: string; dir: 1 | -1 }> = additive
        ? prev.filter((s) => s.key !== key)
        : []
      next.push({ key, dir: existing?.dir === -1 ? 1 : -1 })
      return next
    })
  }

  if (ac.acNumber === null || query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  return (
    <div className="space-y-3">
      <FixtureBanner note={query.data?.fixture} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">{t('booths.heading')}</h1>
        <div className="flex flex-wrap items-center gap-1.5">
          <select className="select text-2xs" value={election}
                  onChange={(e) => setElection(e.target.value)}
                  aria-label={t('common.election')}>
            {['VS-2024', 'VS-2019', 'LS-2024'].map((label) => (
              <option key={label} value={label}>{label}</option>
            ))}
          </select>
          <select className="select text-2xs" value={preset}
                  onChange={(e) => setPreset(e.target.value as keyof typeof PRESETS)}
                  aria-label={t('booths.preset')}>
            {Object.keys(PRESETS).map((key) => (
              <option key={key} value={key}>
                {t(`booths.preset${key[0].toUpperCase()}${key.slice(1)}`)}
              </option>
            ))}
          </select>
          <button className="btn px-2 py-1 text-2xs"
                  onClick={() => { setFilters({}); setSorts([]) }}>
            {t('booths.clear')}
          </button>
          <button
            className="btn px-2 py-1 text-2xs"
            disabled={!rows.length}
            onClick={() => void downloadCsv(
              ac.path(`/results/${encodeURIComponent(election)}/booths`),
              `ac${ac.acNumber}_${election}_booths.csv`,
            )}
          >
            {t('common.export')}
          </button>
        </div>
      </div>

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('booths.showing', { shown: rows.length, total: query.data?.count ?? 0 })}
        {' · '}
        {t('booths.sortHint')}
      </p>

      {rows.length === 0 ? (
        <Empty hint={`${t('overview.noResultsYet')} python -m ingest.parse_form20 <pdf> --election ${election} --load`} />
      ) : (
        <div className="card overflow-auto px-0 py-0" style={{ maxHeight: '70vh' }}>
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10" style={{ background: 'var(--surface-1)' }}>
              <tr className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                {visible.map((c, i) => {
                  const sort = sorts.find((s) => s.key === c.key)
                  return (
                    <th
                      key={c.key}
                      className={`px-3 py-2 ${c.align === 'right' ? 'text-right' : 'text-left'} ${
                        i === 0 ? 'sticky left-0 z-20' : ''
                      }`}
                      style={i === 0 ? { background: 'var(--surface-1)' } : undefined}
                    >
                      <button
                        className="font-medium"
                        onClick={(e) => c.sort && toggleSort(c.key, e.shiftKey)}
                        title={c.sort ? t('booths.sortHint') : undefined}
                      >
                        {c.label}
                        {sort && <span aria-hidden>{sort.dir === 1 ? ' ▲' : ' ▼'}</span>}
                      </button>
                    </th>
                  )
                })}
              </tr>
              <tr>
                {visible.map((c, i) => (
                  <th key={c.key}
                      className={`px-2 pb-1.5 ${i === 0 ? 'sticky left-0 z-20' : ''}`}
                      style={i === 0 ? { background: 'var(--surface-1)' } : undefined}>
                    {c.filter && (
                      <input
                        className="input w-full text-3xs"
                        value={filters[c.key] ?? ''}
                        placeholder={t('booths.filter')}
                        onChange={(e) =>
                          setFilters((f) => ({ ...f, [c.key]: e.target.value }))}
                        aria-label={`${t('booths.filter')} ${c.label}`}
                      />
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.booth_uid} className="border-t"
                    style={{ borderColor: 'var(--surface-2)' }}>
                  {visible.map((c, i) => (
                    <td
                      key={c.key}
                      className={`px-3 py-1.5 ${c.align === 'right' ? 'text-right tnum' : ''} ${
                        i === 0 ? 'sticky left-0' : ''
                      }`}
                      style={i === 0 ? { background: 'var(--surface-1)' } : undefined}
                    >
                      {c.render(r, { t, hi })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('booths.dashNote')}
      </p>

      {selected && (
        <BoothDrawer boothUid={selected} ac={ac} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
