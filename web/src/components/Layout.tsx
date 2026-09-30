import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import AcSwitcher from './AcSwitcher'
import ChatPanel from './ChatPanel'
import ThemeToggle from './ThemeToggle'
import type { AcState } from '../lib/ac'
import type { AppConfig, Me } from '../lib/api'

interface Props {
  me?: Me
  /** From GET /config. Undefined while it is in flight, which is treated as
   *  "chat off": showing a panel that then vanishes is worse than showing it a
   *  beat late, and the assistant is parked by default anyway. */
  config?: AppConfig
  ac: AcState
  onSignOut: () => void
  children: React.ReactNode
}

export default function Layout({ me, config, ac, onSignOut, children }: Props) {
  const { t, i18n } = useTranslation()
  const [chatOpen, setChatOpen] = useState(false)
  const chatEnabled = config?.chat_enabled === true
  const chatVisible = chatEnabled && chatOpen
  const [navOpen, setNavOpen] = useState(false)

  const links = [
    { to: '/', key: 'overview' },
    { to: '/map', key: 'map' },
    { to: '/booths', key: 'booths' },
    { to: '/results', key: 'results' },
    { to: '/voters', key: 'voters' },
    ...(me?.sees_caste
      ? [{ to: '/caste', key: 'caste' }, { to: '/caste-scatter', key: 'casteScatter' }]
      : []),
    { to: '/transfer', key: 'transfer' },
    { to: '/local', key: 'local' },
    { to: '/candidates', key: 'candidates' },
    { to: '/local-politics', key: 'localPolitics' },
    { to: '/news', key: 'news' },
    { to: '/factors', key: 'factors' },
    { to: '/compare', key: 'compare' },
    { to: '/scenario', key: 'scenario' },
    ...(me?.role === 'admin' ? [{ to: '/admin', key: 'admin' }] : []),
  ]

  const toggleLanguage = () => void i18n.changeLanguage(i18n.language === 'hi' ? 'en' : 'hi')

  return (
    <div className="min-h-screen">
      <header
        className="sticky top-0 z-30 border-b"
        style={{ background: 'var(--surface-1)', borderColor: 'var(--gridline)' }}
      >
        <div className="mx-auto flex max-w-[1600px] items-center gap-3 px-4 py-2.5">
          <button
            className="btn px-2 py-1 md:hidden"
            onClick={() => setNavOpen((v) => !v)}
            aria-label="Menu"
            aria-expanded={navOpen}
          >
            ☰
          </button>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{t('app.short')}</div>
            <div className="truncate text-2xs" style={{ color: 'var(--text-muted)' }}>
              {t('app.constituency')}
            </div>
          </div>

          <nav className="ml-4 hidden flex-1 flex-wrap gap-0.5 md:flex">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                className={({ isActive }) =>
                  `rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                    isActive ? 'font-semibold' : ''
                  }`
                }
                style={({ isActive }) => ({
                  background: isActive ? 'var(--surface-2)' : 'transparent',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                })}
              >
                {t(`nav.${link.key}`)}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1.5">
            <AcSwitcher state={ac} />
            <button className="btn px-2 py-1 text-2xs" onClick={toggleLanguage}>
              {t('common.language')}
            </button>
            <ThemeToggle />
            {chatEnabled && (
              <button
                className={`btn px-2 py-1 text-2xs ${chatOpen ? 'btn-primary' : ''}`}
                onClick={() => setChatOpen((v) => !v)}
                aria-pressed={chatOpen}
              >
                {t('chat.title')}
              </button>
            )}
            {me && (
              <div className="hidden items-center gap-1.5 sm:flex">
                <span className="chip" title={me.role}>
                  {me.name}
                </span>
                <button className="btn px-2 py-1 text-2xs" onClick={onSignOut}>
                  {t('common.signOut')}
                </button>
              </div>
            )}
          </div>
        </div>

        {navOpen && (
          <nav className="flex flex-wrap gap-1 border-t px-4 py-2 md:hidden"
               style={{ borderColor: 'var(--gridline)' }}>
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                onClick={() => setNavOpen(false)}
                className="rounded-lg px-2.5 py-1.5 text-sm"
                style={{ color: 'var(--text-secondary)' }}
              >
                {t(`nav.${link.key}`)}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      <div className="mx-auto flex max-w-[1600px] gap-4 px-4 py-4">
        <main className="min-w-0 flex-1">{children}</main>
        {chatVisible && (
          <aside className="hidden w-[380px] shrink-0 lg:block">
            <div className="sticky top-[4.5rem]">
              <ChatPanel isAdmin={me?.role === 'admin'} onClose={() => setChatOpen(false)} />
            </div>
          </aside>
        )}
      </div>

      {chatVisible && (
        <div className="fixed inset-0 z-40 lg:hidden" style={{ background: 'var(--plane)' }}>
          <ChatPanel isAdmin={me?.role === 'admin'} onClose={() => setChatOpen(false)} />
        </div>
      )}

      <footer
        className="mx-auto max-w-[1600px] px-4 pb-6 pt-2 text-2xs"
        style={{ color: 'var(--text-muted)' }}
      >
        Community figures are estimates with confidence scores, not facts. No individual voter
        records are held. Review every figure for accuracy and completeness before relying on it.
      </footer>
    </div>
  )
}
