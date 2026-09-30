import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

export interface Column<T> {
  key: string
  header: string
  render?: (row: T) => React.ReactNode
  value?: (row: T) => number | string | null | undefined
  align?: 'left' | 'right'
  numeric?: boolean
  width?: string
}

interface Props<T> {
  rows: T[]
  columns: Column<T>[]
  onRowClick?: (row: T) => void
  initialSort?: { key: string; dir: 'asc' | 'desc' }
  caption?: string
  maxHeight?: string
}

/** Sortable table. It doubles as the accessible table view every chart on the
 *  page can fall back to, which is why it is plain markup rather than a grid. */
export default function DataTable<T extends Record<string, unknown>>({
  rows, columns, onRowClick, initialSort, caption, maxHeight = '32rem',
}: Props<T>) {
  const { t } = useTranslation()
  const [sort, setSort] = useState(initialSort)

  const sorted = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((c) => c.key === sort.key)
    if (!column) return rows
    const read = (row: T) => column.value?.(row) ?? (row[column.key] as number | string | null)
    return [...rows].sort((a, b) => {
      const av = read(a)
      const bv = read(b)
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av).localeCompare(String(bv), undefined, { numeric: true })
      return sort.dir === 'asc' ? cmp : -cmp
    })
  }, [rows, columns, sort])

  const toggle = (key: string) =>
    setSort((prev) =>
      prev?.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' },
    )

  if (!rows.length) {
    return (
      <div className="card px-4 py-6 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>
        {t('common.noData')}
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full border-collapse">
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr>
              {columns.map((column) => {
                const active = sort?.key === column.key
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className="th cursor-pointer select-none"
                    style={{ width: column.width, textAlign: column.align ?? (column.numeric ? 'right' : 'left') }}
                    onClick={() => toggle(column.key)}
                    aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    {column.header}
                    <span aria-hidden style={{ opacity: active ? 1 : 0.25 }}>
                      {active && sort!.dir === 'asc' ? ' ▲' : ' ▼'}
                    </span>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, index) => (
              <tr
                key={index}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={onRowClick ? 'cursor-pointer' : ''}
                style={{ background: index % 2 ? 'var(--surface-1)' : 'transparent' }}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`td ${column.numeric ? 'tnum' : ''}`}
                    style={{ textAlign: column.align ?? (column.numeric ? 'right' : 'left') }}
                  >
                    {column.render ? column.render(row) : String(row[column.key] ?? '—')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t px-3 py-1.5 text-2xs"
           style={{ borderColor: 'var(--gridline)', color: 'var(--text-muted)' }}>
        {sorted.length} {t('common.booths')}
      </div>
    </div>
  )
}
