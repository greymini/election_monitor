import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import BoothDrawer from '../components/BoothDrawer'
import DataTable, { type Column } from '../components/DataTable'
import PartyChip from '../components/PartyChip'
import { ErrorState, Loading } from '../components/States'
import { api, downloadCsv } from '../lib/api'
import { num, pct } from '../lib/format'
import type { AcState } from '../lib/ac'

interface Row {
  booth_uid: string; ps_numbers: string | null
  area_hi: string; area_en: string; block_en: string
  building: string | null; electors: number | null; votes_polled: number | null
  turnout_pct: number | null
  jmm: number; bjp: number; ajsu: number; jlkm: number; others: number; nota: number
  winner_party: string | null; runner_party: string | null
  margin_votes: number | null; margin_pct: number | null
  source_doc: string | null; source_page: number | null
  [key: string]: unknown
}

interface ElectionOption { label: string; type: string; year: number; is_baseline: boolean }

export default function Results({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const params = useParams()
  const hi = i18n.language === 'hi'
  const [selected, setSelected] = useState<string | null>(null)

  const meta = useQuery<{ elections: ElectionOption[] }>({
    queryKey: ['areas', ac.acNumber], queryFn: () => api.get(ac.path('/areas')),
    enabled: ac.acNumber !== null,
  })
  const elections = meta.data?.elections ?? []
  const [election, setElection] = useState<string>('')
  // A choice made in the dropdown wins over the URL. It was the other way
  // round, so on /results/VS-2019 the dropdown changed nothing.
  const active = election || params.electionLabel || ''
  const chosen = active || elections.find((e) => e.is_baseline)?.label || elections[0]?.label || ''

  const query = useQuery<{ rows: Row[] }>({
    queryKey: ['results', ac.acNumber, chosen],
    queryFn: () => api.get(ac.path(`/results/${encodeURIComponent(chosen)}/booths`)),
    enabled: ac.acNumber !== null && Boolean(chosen),
  })

  const columns = useMemo<Column<Row>[]>(() => [
    { key: 'booth_uid', header: t('common.booth'), width: '6rem' },
    {
      key: 'area', header: t('common.area'),
      render: (row) => (hi ? row.area_hi : row.area_en),
      value: (row) => (hi ? row.area_hi : row.area_en),
    },
    { key: 'electors', header: t('common.electors'), numeric: true,
      render: (row) => num(row.electors) },
    { key: 'votes_polled', header: t('common.votes'), numeric: true,
      render: (row) => num(row.votes_polled) },
    { key: 'turnout_pct', header: t('common.turnout'), numeric: true,
      render: (row) => pct(row.turnout_pct) },
    { key: 'jmm', header: 'JMM', numeric: true, render: (row) => num(row.jmm) },
    { key: 'bjp', header: 'BJP', numeric: true, render: (row) => num(row.bjp) },
    { key: 'jlkm', header: 'JLKM', numeric: true, render: (row) => num(row.jlkm) },
    { key: 'ajsu', header: 'AJSU', numeric: true, render: (row) => num(row.ajsu) },
    { key: 'others', header: 'Other', numeric: true, render: (row) => num(row.others) },
    { key: 'nota', header: 'NOTA', numeric: true, render: (row) => num(row.nota) },
    { key: 'winner_party', header: t('common.winner'),
      render: (row) => <PartyChip abbr={row.winner_party} /> },
    { key: 'margin_votes', header: t('common.margin'), numeric: true,
      render: (row) => `${num(row.margin_votes)} (${pct(row.margin_pct)})` },
    { key: 'source', header: t('common.source'),
      render: (row) => (
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {row.source_doc ? `${row.source_doc} p.${row.source_page ?? '?'}` : '—'}
        </span>
      ),
      value: (row) => row.source_doc ?? '' },
  ], [t, hi])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('results.heading')}</h1>
        <select className="field ml-auto" value={chosen} aria-label={t('common.election')}
                onChange={(e) => setElection(e.target.value)}>
          {elections.map((e) => (
            <option key={e.label} value={e.label}>{e.label}</option>
          ))}
        </select>
        <button
          className="btn"
          onClick={() => void downloadCsv(
            ac.path(`/results/${encodeURIComponent(chosen)}/booths`),
            `${chosen.replace(/\s+/g, '_')}_booths.csv`,
          )}
          disabled={!query.data?.rows.length}
        >
          {t('common.export')}
        </button>
      </div>

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('results.sourceNote')}
      </p>

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && (
        <DataTable
          rows={query.data.rows}
          columns={columns}
          initialSort={{ key: 'booth_uid', dir: 'asc' }}
          onRowClick={(row) => setSelected(row.booth_uid)}
          caption={`Booth-wise results for ${chosen}`}
          maxHeight="36rem"
        />
      )}

      {selected && <BoothDrawer boothUid={selected} ac={ac} onClose={() => setSelected(null)} />}
    </div>
  )
}
