import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import DataTable, { type Column } from '../components/DataTable'
import DivergingLegend from '../components/DivergingLegend'
import PartyChip from '../components/PartyChip'
import { Empty, ErrorState, Loading } from '../components/States'
import { api, downloadCsv } from '../lib/api'
import { num, pct, signed } from '../lib/format'
import { divergingColor } from '../lib/tokens'

interface Row {
  booth_uid: string; area_hi: string; area_en: string; block_id: number
  party: string | null
  ls_votes: number; vs_votes: number; delta_votes: number
  ls_share_pct: number; vs_share_pct: number; delta_share_pct: number
  floating_pct: number | null
  [key: string]: unknown
}

export default function Transfer() {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [party, setParty] = useState('')

  const query = useQuery<{ rows: Row[]; note: string }>({
    queryKey: ['transfer'], queryFn: () => api.get('/transfer?year=2024'),
  })
  const all = query.data?.rows ?? []
  const parties = useMemo(
    () => [...new Set(all.map((r) => r.party).filter(Boolean) as string[])].sort(),
    [all],
  )
  const rows = party ? all.filter((r) => r.party === party) : all

  const columns = useMemo<Column<Row>[]>(() => [
    { key: 'booth_uid', header: t('common.booth'), width: '6rem' },
    { key: 'area', header: t('common.area'),
      render: (r) => (hi ? r.area_hi : r.area_en),
      value: (r) => (hi ? r.area_hi : r.area_en) },
    { key: 'party', header: t('common.party'), render: (r) => <PartyChip abbr={r.party} /> },
    { key: 'ls_votes', header: 'LS votes', numeric: true, render: (r) => num(r.ls_votes) },
    { key: 'vs_votes', header: 'VS votes', numeric: true, render: (r) => num(r.vs_votes) },
    { key: 'ls_share_pct', header: 'LS %', numeric: true, render: (r) => pct(r.ls_share_pct) },
    { key: 'vs_share_pct', header: 'VS %', numeric: true, render: (r) => pct(r.vs_share_pct) },
    { key: 'delta_share_pct', header: 'Change (pp)', numeric: true,
      render: (r) => (
        <span className="inline-flex items-center justify-end gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: divergingColor(-r.delta_share_pct, 20) }} />
          {signed(r.delta_share_pct)}
        </span>
      ) },
    { key: 'floating_pct', header: 'Floating %', numeric: true, render: (r) => pct(r.floating_pct) },
  ], [t, hi])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('transfer.heading')}</h1>
        <select className="field ml-auto" value={party} onChange={(e) => setParty(e.target.value)}>
          <option value="">{t('common.all')}</option>
          {parties.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <button className="btn" disabled={!rows.length}
                onClick={() => void downloadCsv('/transfer?year=2024', 'transfer_2024.csv')}>
          {t('common.export')}
        </button>
      </div>

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('transfer.note')}</p>

      <DivergingLegend saturateAt={20} title="Change in share, LS to VS" />

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && !rows.length && (
        <Empty hint="Both LS-2024 and VS-2024 Form 20 must be loaded and crosswalked before transfer can be computed." />
      )}

      {rows.length > 0 && (
        <>
          <DataTable rows={rows} columns={columns}
                     initialSort={{ key: 'floating_pct', dir: 'desc' }}
                     caption="Lok Sabha to Vidhan Sabha vote movement per booth" />
          <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            Floating % is the Pedersen index between the two 2024 polls: half the sum of absolute
            share changes. High values mark booths that genuinely voted differently six months
            apart — the movable vote a by-election turns on.
          </p>
        </>
      )}
    </div>
  )
}
