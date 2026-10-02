import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

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

// Parse "gazette | SC | F | unopposed" → { reservation, gender, unopposed }
function parseGazetteTag(tag: string | null): { reservation: string; gender: string; unopposed: boolean } {
  if (!tag || !tag.startsWith('gazette')) return { reservation: '', gender: '', unopposed: false }
  const parts = tag.split(' | ').map((p) => p.trim())
  return {
    reservation: parts[1] ?? '',
    gender: parts[2] ?? '',
    unopposed: parts.includes('unopposed'),
  }
}

const RESERVATION_COLOURS: Record<string, string> = {
  SC: '#dc2626', ST: '#d97706', UR: '#2563eb', BC: '#7c3aed',
}
function ReservationBadge({ res }: { res: string }) {
  if (!res || res === 'UR') return null
  return (
    <span className="inline-block rounded px-1 py-0.5 text-2xs font-medium text-white ml-1"
          style={{ background: RESERVATION_COLOURS[res] ?? '#6b7280', fontSize: '10px' }}>
      {res}
    </span>
  )
}

interface CasteRow {
  area_id: number; area_en: string; area_hi: string | null; block_en: string
  community: string; estimated_voters: number; community_pct: number
  estimated_votes: number | null
}

// Stable community colour palette (colourblind-safe)
const COMMUNITY_COLOURS: Record<string, string> = {
  GEN: '#2563eb',
  OBC: '#16a34a',
  SC:  '#dc2626',
  ST:  '#d97706',
  MUSLIM: '#7c3aed',
  OTHER: '#6b7280',
}
function communityColour(name: string): string {
  const key = name.toUpperCase()
  return COMMUNITY_COLOURS[key] ?? COMMUNITY_COLOURS.OTHER
}

function CasteBreakdown({ ac }: { ac: AcState }) {
  const [open, setOpen] = useState(false)
  const [selectedArea, setSelectedArea] = useState<number | null>(null)

  const casteQuery = useQuery<{ rows: CasteRow[]; note: string }>({
    queryKey: ['local-caste', ac.acNumber],
    queryFn: () => api.get(ac.path('/local-caste')),
    enabled: ac.acNumber !== null && open,
  })

  const rows = casteQuery.data?.rows ?? []

  // Pivot: area → [{community, pct, votes}]
  const byArea = useMemo(() => {
    const m = new Map<number, { area_en: string; block_en: string; data: CasteRow[] }>()
    for (const r of rows) {
      if (!m.has(r.area_id)) m.set(r.area_id, { area_en: r.area_en, block_en: r.block_en, data: [] })
      m.get(r.area_id)!.data.push(r)
    }
    return m
  }, [rows])

  const areas = useMemo(() => Array.from(byArea.entries()).sort((a, b) =>
    a[1].area_en.localeCompare(b[1].area_en)), [byArea])

  // Bar chart data for selected area
  const chartData = useMemo(() => {
    if (selectedArea === null) return []
    const entry = byArea.get(selectedArea)
    if (!entry) return []
    return entry.data.map((r) => ({
      community: r.community,
      'Estimated voters': r.estimated_voters,
      'Community %': r.community_pct,
      ...(r.estimated_votes != null ? { 'Est. votes cast': r.estimated_votes } : {}),
    }))
  }, [selectedArea, byArea])

  return (
    <div className="card">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-3 font-medium"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Caste Breakdown by Panchayat</span>
        <span aria-hidden>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-4">
          <p className="text-2xs" style={{ color: 'var(--text-secondary)' }}>
            Estimated community composition per GP, derived from booth-level caste data.
            Figures are approximations — not census counts.
            {rows.length === 0 && !casteQuery.isLoading && (
              <span style={{ color: 'var(--status-warning)' }}>
                {' '}No caste data loaded for this AC yet.
              </span>
            )}
          </p>

          {casteQuery.isLoading && <Loading />}
          {casteQuery.isError && (
            <ErrorState error={casteQuery.error} onRetry={() => void casteQuery.refetch()} />
          )}

          {rows.length > 0 && (
            <div className="flex flex-wrap gap-4">
              {/* GP selector */}
              <div className="flex-none w-56">
                <label className="text-xs font-medium block mb-1">Select GP</label>
                <select
                  className="field w-full text-sm"
                  value={selectedArea ?? ''}
                  onChange={(e) => setSelectedArea(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">— pick a panchayat —</option>
                  {areas.map(([id, { area_en, block_en }]) => (
                    <option key={id} value={id}>{block_en} / {area_en}</option>
                  ))}
                </select>
              </div>

              {/* Bar chart */}
              {selectedArea !== null && chartData.length > 0 && (
                <div className="flex-1 min-w-64">
                  <p className="text-xs font-medium mb-2">
                    {byArea.get(selectedArea)?.area_en} — community share
                  </p>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={chartData} layout="vertical"
                              margin={{ top: 0, right: 24, bottom: 0, left: 48 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" unit="%" domain={[0, 100]} tick={{ fontSize: 11 }} />
                      <YAxis type="category" dataKey="community" tick={{ fontSize: 11 }} width={60} />
                      <Tooltip
                        formatter={(val, name) =>
                          name === 'Community %' ? [`${val}%`, name]
                          : [num(val as number), name]}
                      />
                      <Bar dataKey="Community %"
                           fill="#2563eb"
                           label={{ position: 'right', fontSize: 11, formatter: (v: number) => `${v}%` }}>
                        {chartData.map((entry) => (
                          <rect key={entry.community} fill={communityColour(entry.community)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                  {/* Summary table */}
                  <table className="mt-3 w-full text-xs border-collapse">
                    <thead>
                      <tr>
                        {['Community', 'Est. voters', '%',
                          ...(chartData[0]?.['Est. votes cast'] != null ? ['Est. votes'] : [])
                        ].map((h) => (
                          <th key={h} className="border-b py-1 px-2 text-left font-medium"
                              style={{ color: 'var(--text-secondary)' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(byArea.get(selectedArea)?.data ?? []).map((r) => (
                        <tr key={r.community}>
                          <td className="py-1 px-2">
                            <span className="inline-block w-2 h-2 rounded-sm mr-1"
                                  style={{ background: communityColour(r.community) }} />
                            {r.community}
                          </td>
                          <td className="py-1 px-2 text-right tabular-nums">{num(r.estimated_voters)}</td>
                          <td className="py-1 px-2 text-right tabular-nums">{r.community_pct}%</td>
                          {r.estimated_votes != null && (
                            <td className="py-1 px-2 text-right tabular-nums">{num(r.estimated_votes)}</td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function LocalPolls({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [seatType, setSeatType] = useState('')

  const query = useQuery<{ rows: Row[]; note: string }>({
    queryKey: ['local', ac.acNumber, seatType],
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
    { key: 'winner', header: t('common.winner'),
      render: (r) => {
        const { unopposed } = parseGazetteTag(r.tag_source)
        return (
          <span>
            {r.winner}
            {unopposed && (
              <span className="ml-1.5 rounded px-1 py-0.5 text-2xs font-medium"
                    style={{ background: 'var(--status-ok-bg)', color: 'var(--status-ok)',
                             fontSize: '10px' }}
                    title="Returned unopposed">
                Uncontested
              </span>
            )}
          </span>
        )
      } },
    { key: 'reservation', header: 'Res.',
      render: (r) => {
        const { reservation, gender } = parseGazetteTag(r.tag_source)
        if (!reservation) return <span style={{ color: 'var(--text-muted)' }}>—</span>
        return (
          <span>
            <ReservationBadge res={reservation} />
            {gender === 'F' && (
              <span className="ml-1" style={{ color: 'var(--text-secondary)', fontSize: '11px' }}
                    title="Female reserved seat">♀</span>
            )}
          </span>
        )
      },
      value: (r) => parseGazetteTag(r.tag_source).reservation },
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

      <CasteBreakdown ac={ac} />
    </div>
  )
}
