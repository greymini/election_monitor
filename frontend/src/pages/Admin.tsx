import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import StatTile from '../components/StatTile'
import { Empty, ErrorState, Loading } from '../components/States'
import DataHealthStrip, { type DataHealth } from '../components/DataHealth'
import { api } from '../lib/api'
import { dateShort, num } from '../lib/format'
import type { AcState } from '../lib/ac'

interface ReviewItem {
  id: number; kind: string; ref: string; payload: unknown
  status: string; note: string | null; created_at: string
}
interface Usage {
  month_to_date: { cost_usd: number; calls: number }
  cache_hit_rate: { pct: number | null } | null
  by_day: Array<{ day: string; model: string; purpose: string; cost_usd: number; calls: number }>
  by_user_today: Array<{ name: string; role: string; tokens: number; cost_usd: number }>
}
interface Jobs {
  last_per_job: Array<{ job: string; started: string; finished: string | null; status: string }>
}

// 'data' first: it is the screen an operator opens this page for, and the
// data-health strip moved here from the Overview. Row counts and shell
// commands belong on an admin screen, not above a strategist's margin chart -
// and this route is already admin-only in App.tsx, so a non-admin never
// reaches a command at all.
const TABS = ['data', 'review', 'usage', 'jobs'] as const

export default function Admin({ ac }: { ac: AcState }) {
  const { t, i18n } = useTranslation()
  const [tab, setTab] = useState<(typeof TABS)[number]>('data')
  const client = useQueryClient()

  const review = useQuery<{
    rows: ReviewItem[]; open_by_kind: Array<{ kind: string; open: number }>
  }>({
    queryKey: ['review-queue', ac.acNumber],
    queryFn: () => api.get(ac.path('/admin/review-queue')),
    enabled: tab === 'review' && ac.acNumber !== null,
  })
  const usage = useQuery<Usage>({
    queryKey: ['usage', ac.acNumber], queryFn: () => api.get(ac.path('/admin/usage')),
    enabled: tab === 'usage' && ac.acNumber !== null,
  })
  const jobs = useQuery<Jobs>({
    queryKey: ['jobs', ac.acNumber], queryFn: () => api.get(ac.path('/admin/jobs')),
    enabled: tab === 'jobs' && ac.acNumber !== null,
  })
  // The data-health block comes from /summary, which already computes it for
  // every AC. No new endpoint: the Overview was reading the same field.
  const health = useQuery<{ data_health: DataHealth; constituency: { ac_number: number } }>({
    queryKey: ['summary', ac.acNumber],
    queryFn: () => api.get(ac.path('/summary')),
    enabled: tab === 'data' && ac.acNumber !== null,
  })

  const resolve = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'resolved' | 'rejected' }) =>
      api.post(ac.path(`/admin/review-queue/${id}`), { status }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['review-queue'] }),
  })

  const cacheRate = usage.data?.cache_hit_rate?.pct ?? null

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('nav.admin')}</h1>
        <div className="ml-auto flex gap-1">
          {TABS.map((name) => (
            <button key={name} className={`btn text-2xs ${tab === name ? 'btn-primary' : ''}`}
                    onClick={() => setTab(name)}>
              {name}
            </button>
          ))}
        </div>
      </div>

      {tab === 'data' && (
        <div className="space-y-3">
          {health.isLoading && <Loading />}
          {health.isError && (
            <ErrorState error={health.error} onRetry={() => void health.refetch()} />
          )}
          {health.data && (
            <>
              <DataHealthStrip
                health={health.data.data_health}
                acNumber={health.data.constituency.ac_number}
              />
              <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                {t('admin.dataNote')}
              </p>
            </>
          )}
        </div>
      )}

      {tab === 'review' && (
        <>
          {review.isLoading && <Loading />}
          {review.isError && <ErrorState error={review.error} />}
          {review.data && (
            <>
              <div className="flex flex-wrap gap-2">
                {review.data.open_by_kind.map((row) => (
                  <span key={row.kind} className="chip">{row.kind}: {row.open}</span>
                ))}
              </div>
              {!review.data.rows.length && <Empty hint="Nothing is waiting for review." />}
              <div className="space-y-2">
                {review.data.rows.map((item) => (
                  <div key={item.id} className="card px-4 py-3">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="chip">{item.kind}</span>
                      <span className="font-mono text-2xs">{item.ref}</span>
                      <span className="ml-auto text-2xs" style={{ color: 'var(--text-muted)' }}>
                        {dateShort(item.created_at, i18n.language)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm">{item.note}</p>
                    <pre className="mt-1.5 max-h-32 overflow-auto rounded p-2 text-2xs"
                         style={{ background: 'var(--surface-2)' }}>
                      {JSON.stringify(item.payload, null, 1)}
                    </pre>
                    <div className="mt-2 flex gap-1.5">
                      <button className="btn text-2xs"
                              onClick={() => resolve.mutate({ id: item.id, status: 'resolved' })}>
                        Resolved
                      </button>
                      <button className="btn text-2xs"
                              onClick={() => resolve.mutate({ id: item.id, status: 'rejected' })}>
                        Not an issue
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}

      {tab === 'usage' && (
        <>
          {usage.isLoading && <Loading />}
          {usage.isError && <ErrorState error={usage.error} />}
          {usage.data && (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <StatTile
                  label="Month to date"
                  value={`$${Number(usage.data.month_to_date?.cost_usd ?? 0).toFixed(2)}`}
                  sub={`${num(usage.data.month_to_date?.calls)} calls`}
                />
                <StatTile
                  label="Cache hit rate"
                  value={cacheRate != null ? `${cacheRate}%` : '—'}
                  sub="Should exceed 90% in a live session"
                  status={cacheRate != null && cacheRate >= 90 ? 'good' : 'warning'}
                  statusText={cacheRate != null && cacheRate >= 90
                    ? 'Healthy'
                    : 'Check for a changing prompt prefix'}
                />
                <StatTile label="Users today" value={num(usage.data.by_user_today.length)} />
              </div>

              <section className="card overflow-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <th className="th">Day</th>
                      <th className="th">Model</th>
                      <th className="th">Purpose</th>
                      <th className="th" style={{ textAlign: 'right' }}>Calls</th>
                      <th className="th" style={{ textAlign: 'right' }}>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data.by_day.map((row, i) => (
                      <tr key={i}>
                        <td className="td">{row.day}</td>
                        <td className="td text-2xs">{row.model}</td>
                        <td className="td text-2xs">{row.purpose}</td>
                        <td className="td tnum" style={{ textAlign: 'right' }}>{num(row.calls)}</td>
                        <td className="td tnum" style={{ textAlign: 'right' }}>
                          ${Number(row.cost_usd).toFixed(4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </>
      )}

      {tab === 'jobs' && (
        <>
          {jobs.isLoading && <Loading />}
          {jobs.isError && <ErrorState error={jobs.error} />}
          {jobs.data && (
            <section className="card overflow-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    <th className="th">Job</th>
                    <th className="th">Started</th>
                    <th className="th">Finished</th>
                    <th className="th">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.data.last_per_job.map((row) => {
                    const colour = row.status === 'ok'
                      ? 'var(--status-good)'
                      : row.status === 'failed'
                        ? 'var(--status-critical)'
                        : 'var(--text-secondary)'
                    const icon = row.status === 'ok' ? '✓' : row.status === 'failed' ? '×' : '·'
                    return (
                      <tr key={row.job}>
                        <td className="td font-mono text-2xs">{row.job}</td>
                        <td className="td text-2xs">{dateShort(row.started, i18n.language)}</td>
                        <td className="td text-2xs">
                          {row.finished ? dateShort(row.finished, i18n.language) : '—'}
                        </td>
                        <td className="td text-2xs" style={{ color: colour }}>
                          <span aria-hidden>{icon}</span> {row.status}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  )
}
