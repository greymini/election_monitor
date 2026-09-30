import { useTranslation } from 'react-i18next'

export function Loading({ label }: { label?: string }) {
  const { t } = useTranslation()
  return (
    <div className="card flex items-center gap-2 px-4 py-8 text-sm"
         style={{ color: 'var(--text-secondary)' }}>
      <span
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
        aria-hidden
      />
      {label ?? t('common.loading')}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation()
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="card px-4 py-6">
      <div className="flex items-center gap-1.5 text-sm font-medium"
           style={{ color: 'var(--status-critical)' }}>
        <span aria-hidden>×</span>
        {t('common.error')}
      </div>
      <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>{message}</p>
      {onRetry && (
        <button className="btn mt-3" onClick={onRetry}>
          {t('common.retry')}
        </button>
      )}
    </div>
  )
}

/** "Data not available" is a real answer here, and it should say what is missing. */
export function Empty({ hint }: { hint?: string }) {
  const { t } = useTranslation()
  return (
    <div className="card px-4 py-8 text-center">
      <div className="text-sm font-medium">{t('common.noData')}</div>
      {hint && (
        <p className="mx-auto mt-1 max-w-md text-2xs" style={{ color: 'var(--text-secondary)' }}>
          {hint}
        </p>
      )}
    </div>
  )
}
