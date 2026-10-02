import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Catches a render error in one page so it cannot blank the whole app.
 *
 * Without it, any throw during render - a field the API did not send, a null
 * where an object was expected - unmounted everything including the header,
 * leaving a white screen with no way to navigate or sign out. `resetKey`
 * clears the error when it changes (App passes the route and AC), so moving to
 * another page recovers.
 */
interface Props { children: ReactNode; resetKey?: string }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page crashed', error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) return <Crashed error={this.state.error} />
    return this.props.children
  }
}

function Crashed({ error }: { error: Error }) {
  const { t } = useTranslation()
  return (
    <div className="card px-4 py-6" role="alert">
      <div className="text-sm font-medium" style={{ color: 'var(--status-critical)' }}>
        {t('common.crashTitle')}
      </div>
      <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
        {t('common.crashBody')}
      </p>
      <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>{error.message}</p>
      <button className="btn mt-3" onClick={() => window.location.reload()}>
        {t('common.reload')}
      </button>
    </div>
  )
}
