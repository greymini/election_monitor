import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import DataTable, { type Column } from '../components/DataTable'
import PartyChip from '../components/PartyChip'
import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { num } from '../lib/format'
import type { AcState } from '../lib/ac'

interface Row {
  local_result_id: number; election: string; seat_type: string; seat_name: string
  area_hi: string | null; area_en: string | null
  winner: string; runner_up: string | null
  tagged_party: string | null; tag_source: string | null; tag_confidence: number | null
  votes: number | null; runner_up_votes: number | null; margin: number | null
  [key: string]: unknown
}

export default function LocalPolls({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [seatType, setSeatType] = useState('')

  const query = useQuery<{ rows: Row[]; note: string }>({
    queryKey: ['local', seatType],
    queryFn: () => api.get(ac.path(`/local-results${seatType ? `?seat_type=${seatType}` : ''}`)),
    enabled: ac.acNumber !== null,
  })
  const rows = query.data?.rows ?? []

  const columns = useMemo<Column<Row>[]>(() => [
    { key: 'election', header: t('common.election') },
    { key: 'seat_type', header: 'Seat' },
    { key: 'seat_name', header: 'Name' },
    { key: 'area', header: t('common.area'),
      render: (r) => (hi ? r.area_hi : r.area_en) ?? '—',
      value: (r) => (hi ? r.area_hi : r.area_en) ?? '' },
    { key: 'winner', header: t('common.winner') },
    { key: 'tagged_party', header: 'Tagged as',
      render: (r) =>
        r.tagged_party ? (
          <span title={`Tag source: ${r.tag_source ?? 'unrecorded'}`}>
            <PartyChip abbr={r.tagged_party} />
          </span>
        ) : (
          <span style={{ color: 'var(--text-muted)' }}>untagged</span>
        ) },
    { key: 'votes', header: t('common.votes'), numeric: true, render: (r) => num(r.votes) },
    { key: 'margin', header: t('common.margin'), numeric: true, render: (r) => num(r.margin) },
  ], [t, hi])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('nav.local')}</h1>
        <select className="field ml-auto" value={seatType}
                onChange={(e) => setSeatType(e.target.value)}>
          <option value="">{t('common.all')}</option>
          <option value="mukhiya">Mukhiya</option>
          <option value="ZP">Zila Parishad</option>
          <option value="panchayat_samiti">Panchayat Samiti</option>
          <option value="ward">GMC ward</option>
        </select>
      </div>

      <div className="card px-4 py-3">
        <p className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
          <span aria-hidden style={{ color: 'var(--status-warning)' }}>! </span>
          Panchayat elections are contested without party symbols. Any party shown here is a manual
          judgement; the tag source is recorded against each row — hover the chip to see it.
        </p>
      </div>

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && !rows.length && (
        <Empty hint="Transcribe SEC results into a CSV, then: python -m ingest.fetch_sec --load-csv results.csv --election PANCHAYAT-2022" />
      )}
      {rows.length > 0 && (
        <DataTable rows={rows} columns={columns} initialSort={{ key: 'election', dir: 'desc' }}
                   caption="Panchayat and municipal results" />
      )}
    </div>
  )
}
