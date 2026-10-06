import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { login } from '../lib/api'

export default function Login(
  { onSignedIn, notice = null }: { onSignedIn: () => void; notice?: string | null },
) {
  const { t, i18n } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
      onSignedIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm px-6 py-7">
        <div className="text-lg font-semibold">{t('app.title')}</div>
        <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
          {t('app.constituency')}
        </p>

        {notice && !error && (
          <p className="mt-3 text-2xs" role="status" style={{ color: 'var(--status-warning)' }}>
            {notice}
          </p>
        )}

        <label htmlFor="login-user"
               className="mt-5 block text-2xs font-medium uppercase tracking-wide"
               style={{ color: 'var(--text-secondary)' }}>
          {t('login.userId')}
        </label>
        <input
          id="login-user"
          className="field mt-1 w-full"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />

        <label htmlFor="login-password"
               className="mt-3 block text-2xs font-medium uppercase tracking-wide"
               style={{ color: 'var(--text-secondary)' }}>
          {t('login.password')}
        </label>
        <input
          id="login-password"
          className="field mt-1 w-full"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />

        {error && (
          <p className="mt-3 text-2xs" style={{ color: 'var(--status-critical)' }}>
            × {error}
          </p>
        )}

        <button className="btn btn-primary mt-5 w-full justify-center" disabled={busy}>
          {busy ? t('common.loading') : t('login.submit')}
        </button>

        <button
          type="button"
          className="mt-3 w-full text-2xs underline"
          style={{ color: 'var(--text-muted)' }}
          onClick={() => void i18n.changeLanguage(i18n.language === 'hi' ? 'en' : 'hi')}
        >
          {t('common.language')}
        </button>
      </form>
    </div>
  )
}
