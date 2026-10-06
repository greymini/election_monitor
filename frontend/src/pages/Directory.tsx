import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import DataTable, { type Column } from '../components/DataTable'
import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import type { AcState } from '../lib/ac'

type Tab = 'panchayats' | 'polling' | 'officials' | 'calendar'

interface PanchayatRow {
  area_id: number
  name_en: string
  name_hi: string | null
  lgd_code: string | null
  block_en: string
  has_boundary: boolean
  village_count: number
  official_count: number
  mukhiya_2022: string | null
  [key: string]: unknown
}

interface VillageRow { alias: string; script: string | null; source: string | null }

interface PsRow {
  part_number: number
  building_hi: string | null
  village_hi: string | null
  block_en: string | null
  panchayat_en: string | null
  match_status: string | null
  match_score: number | null
  note: string | null
  [key: string]: unknown
}

interface OfficialRow {
  official_id: number
  area_id: number
  panchayat_en: string
  portal_role: string
  office: string
  name: string
  [key: string]: unknown
}

interface CalendarRow {
  label: string
  type: string
  year: number
  poll_date: string | null
  phase: string | null
  is_baseline: boolean
  notes: string | null
  [key: string]: unknown
}

export default function Directory({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [tab, setTab] = useState<Tab>('panchayats')
  const [expanded, setExpanded] = useState<number | null>(null)

  const panchayats = useQuery<{ rows: PanchayatRow[]; source: string }>({
    queryKey: ['directory-panchayats', ac.acNumber],
    queryFn: () => api.get(ac.path('/directory/panchayats')),
    enabled: ac.acNumber !== null && tab === 'panchayats',
  })
  const villages = useQuery<{ rows: VillageRow[]; source: string }>({
    queryKey: ['directory-villages', ac.acNumber, expanded],
    queryFn: () => api.get(ac.path(`/directory/panchayats/${expanded}/villages`)),
    enabled: ac.acNumber !== null && expanded !== null,
  })
  const polling = useQuery<{ rows: PsRow[]; source: string; note: string }>({
    queryKey: ['directory-ps', ac.acNumber],
    queryFn: () => api.get(ac.path('/directory/polling-stations')),
    enabled: ac.acNumber !== null && tab === 'polling',
  })
  const officials = useQuery<{ rows: OfficialRow[]; source: string }>({
    queryKey: ['directory-officials', ac.acNumber],
    queryFn: () => api.get(ac.path('/directory/officials')),
    enabled: ac.acNumber !== null && tab === 'officials',
  })
  const calendar = useQuery<{ rows: CalendarRow[]; source: string }>({
    queryKey: ['directory-calendar', ac.acNumber],
    queryFn: () => api.get(ac.path('/directory/poll-calendar')),
    enabled: ac.acNumber !== null && tab === 'calendar',
  })

  const panchayatCols: Column<PanchayatRow>[] = useMemo(() => [
    { key: 'name', header: t('directory.colName'), render: (r) => (hi ? r.name_hi || r.name_en : r.name_en) },
    { key: 'lgd_code', header: t('directory.colLgd'), render: (r) => r.lgd_code ?? '—' },
    { key: 'block_en', header: t('common.block') },
    { key: 'village_count', header: t('directory.colVillages') },
    { key: 'has_boundary', header: t('directory.colBoundary'), render: (r) => (r.has_boundary ? '✔' : '—') },
    { key: 'official_count', header: t('directory.colOfficials') },
    { key: 'mukhiya_2022', header: t('directory.colMukhiya'), render: (r) => r.mukhiya_2022 ?? '—' },
  ], [t, hi])

  const tabs: { id: Tab; label: string }[] = [
    { id: 'panchayats', label: t('directory.tabPanchayats') },
    { id: 'polling', label: t('directory.tabPolling') },
    { id: 'officials', label: t('directory.tabOfficials') },
    { id: 'calendar', label: t('directory.tabCalendar') },
  ]

  const activeQuery = tab === 'panchayats' ? panchayats
    : tab === 'polling' ? polling
      : tab === 'officials' ? officials
        : calendar

  if (activeQuery.isError) {
    return <ErrorState error={activeQuery.error as Error} onRetry={() => void activeQuery.refetch()} />
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">{t('directory.title')}</h1>
        <p className="mt-1 text-2xs" style={{ color: 'var(--text-secondary)' }}>
          {t('directory.intro')}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            className={`btn text-2xs ${tab === id ? 'btn-primary' : ''}`}
            onClick={() => { setTab(id); setExpanded(null) }}
          >
            {label}
          </button>
        ))}
      </div>

      {activeQuery.isLoading && <Loading />}

      {tab === 'panchayats' && panchayats.data && (
        <>
          <p className="text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('directory.source')}: {panchayats.data.source}
          </p>
          {panchayats.data.rows.length === 0 ? (
            <Empty hint={t('directory.emptyPanchayats')} />
          ) : (
            <>
              <DataTable
                rows={panchayats.data.rows}
                columns={panchayatCols}
                onRowClick={(r) => setExpanded(expanded === r.area_id ? null : r.area_id)}
              />
              {expanded !== null && villages.data && (
                <div className="card p-3 text-2xs">
                  <div className="font-medium">{t('directory.villagesTitle')}</div>
                  <ul className="mt-2 list-disc pl-4">
                    {villages.data.rows.map((v) => (
                      <li key={v.alias}>{v.alias}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'polling' && polling.data && (
        <>
          <p className="text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('directory.source')}: {polling.data.source}
          </p>
          <p className="text-3xs" style={{ color: 'var(--text-secondary)' }}>{polling.data.note}</p>
          {polling.data.rows.length === 0 ? (
            <Empty hint={t('directory.emptyPolling')} />
          ) : (
            <DataTable
              rows={polling.data.rows}
              columns={[
                { key: 'part_number', header: t('directory.colPart') },
                { key: 'building_hi', header: t('directory.colBuilding') },
                { key: 'village_hi', header: t('directory.colVillage') },
                { key: 'block_en', header: t('common.block') },
                { key: 'panchayat_en', header: t('common.area') },
                { key: 'match_status', header: t('directory.colMatch') },
              ]}
            />
          )}
        </>
      )}

      {tab === 'officials' && officials.data && (
        <>
          <p className="text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('directory.source')}: {officials.data.source}
          </p>
          {officials.data.rows.length === 0 ? (
            <Empty hint={t('directory.emptyOfficials')} />
          ) : (
            <DataTable
              rows={officials.data.rows}
              columns={[
                { key: 'panchayat_en', header: t('common.area') },
                { key: 'portal_role', header: t('directory.colRole') },
                { key: 'office', header: t('directory.colOffice') },
                { key: 'name', header: t('directory.colPerson') },
              ]}
            />
          )}
        </>
      )}

      {tab === 'calendar' && calendar.data && (
        <>
          <p className="text-3xs" style={{ color: 'var(--text-muted)' }}>
            {t('directory.source')}: {calendar.data.source}
          </p>
          {calendar.data.rows.length === 0 ? (
            <Empty hint={t('directory.emptyCalendar')} />
          ) : (
            <DataTable
              rows={calendar.data.rows}
              columns={[
                { key: 'label', header: t('common.election') },
                { key: 'poll_date', header: t('directory.colDate') },
                { key: 'phase', header: t('directory.colPhase') },
                { key: 'notes', header: t('directory.colNotes') },
              ]}
            />
          )}
        </>
      )}
    </div>
  )
}
