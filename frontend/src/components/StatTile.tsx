import type { ReactNode } from 'react'

interface Props {
  label: string
  value: ReactNode
  sub?: ReactNode
  accent?: string
  /** A status needs an icon and a label, never colour alone. */
  status?: 'good' | 'warning' | 'serious' | 'critical'
  statusText?: string
}

const STATUS_ICON = { good: '✓', warning: '!', serious: '!', critical: '×' } as const

/** A single headline number is better as a stat tile than as a one-bar chart. */
export default function StatTile({ label, value, sub, accent, status, statusText }: Props) {
  return (
    <div className="card px-4 py-3">
      <div className="text-2xs font-medium uppercase tracking-wide"
           style={{ color: 'var(--text-muted)' }}>
        {label}
      </div>
      <div className="tnum mt-1 text-2xl font-semibold leading-tight"
           style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      {sub && (
        <div className="mt-0.5 text-2xs" style={{ color: 'var(--text-secondary)' }}>
          {sub}
        </div>
      )}
      {status && statusText && (
        <div className="mt-1.5 inline-flex items-center gap-1 text-2xs font-medium"
             style={{ color: `var(--status-${status})` }}>
          <span aria-hidden>{STATUS_ICON[status]}</span>
          <span>{statusText}</span>
        </div>
      )}
    </div>
  )
}
