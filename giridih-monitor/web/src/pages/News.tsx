import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Empty, ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { dateShort, num } from '../lib/format'
import { chartInk, token } from '../lib/tokens'

interface Item {
  news_id: number; published: string; source: string; title: string
  summary_hi: string | null; summary_en: string | null
  issues: string[] | null; parties: string[] | null
  sentiment: number | null; url: string; similarity: number | null
}

interface Issues {
  since: string
  by_issue: Array<{ issue: string; items: number; avg_sentiment: number | null }>
  by_week: Array<{ week: string; items: number }>
  by_party: Array<{ party: string; mentions: number }>
}

export default function News() {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [q, setQ] = useState('')
  const [issue, setIssue] = useState('')
  const [submitted, setSubmitted] = useState('')
  const ink = chartInk()

  const clusters = useQuery<Issues>({
    queryKey: ['news-issues'], queryFn: () => api.get('/news/issues?days=30'),
  })
  const query = useQuery<{ rows: Item[]; issues: string[] }>({
    queryKey: ['news', submitted, issue],
    queryFn: () => {
      const params = new URLSearchParams()
      if (submitted) params.set('q', submitted)
      if (issue) params.set('issue', issue)
      return api.get(`/news?${params.toString()}`)
    },
  })
  const rows = query.data?.rows ?? []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('nav.news')}</h1>
        <form
          className="ml-auto flex gap-1.5"
          onSubmit={(e) => { e.preventDefault(); setSubmitted(q.trim()) }}
        >
          <input className="field w-56" value={q} placeholder={t('common.search')}
                 onChange={(e) => setQ(e.target.value)} />
          <select className="field" value={issue} onChange={(e) => setIssue(e.target.value)}>
            <option value="">{t('common.all')}</option>
            {(query.data?.issues ?? []).map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
          <button className="btn btn-primary">{t('common.search')}</button>
        </form>
      </div>

      {clusters.data && clusters.data.by_issue.length > 0 && (
        <section className="card px-4 py-3">
          <h2 className="text-sm font-semibold">Issues in the last 30 days</h2>
          <div className="mt-2" style={{ height: Math.max(200, clusters.data.by_issue.length * 22) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={clusters.data.by_issue} layout="vertical"
                        margin={{ top: 4, right: 32, left: 8, bottom: 4 }}>
                <CartesianGrid horizontal={false} stroke={ink.grid} />
                <XAxis type="number" tick={{ fontSize: 11, fill: ink.label }}
                       axisLine={{ stroke: ink.axis }} tickLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="issue" width={150}
                       tick={{ fontSize: 11, fill: ink.label }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: 'var(--surface-2)' }}
                         contentStyle={{ background: ink.surface, border: `1px solid ${ink.grid}`,
                                         borderRadius: 8, fontSize: 12, color: ink.text }}
                         formatter={(value: number) => [num(value), 'articles']} />
                <Bar dataKey="items" fill={token('--seq-5')} radius={[0, 4, 4, 0]} maxBarSize={16} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
            Issue tags are applied by a model against a fixed list, and sentiment is per party.
            Both are labels on coverage, not measurements of opinion.
          </p>
        </section>
      )}

      {query.isLoading && <Loading />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && !rows.length && (
        <Empty hint="No labelled articles yet. The crawler runs every four hours and labelling runs nightly; or run: python -m worker.run news.crawl" />
      )}

      <div className="space-y-2">
        {rows.map((item) => (
          <article key={item.news_id} className="card px-4 py-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <a href={item.url} target="_blank" rel="noopener noreferrer"
                 className="text-sm font-medium underline-offset-2 hover:underline">
                {item.title}
              </a>
              <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                {item.source} · {dateShort(item.published, i18n.language)}
                {item.similarity !== null && ` · ${(item.similarity * 100).toFixed(0)}% match`}
              </span>
            </div>
            <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
              {(hi ? item.summary_hi : item.summary_en) ?? item.summary_hi ?? ''}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {(item.issues ?? []).map((i) => <span key={i} className="chip">{i}</span>)}
              {(item.parties ?? []).map((p) => <span key={p} className="chip">{p}</span>)}
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
