import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import AcSwitcher from './AcSwitcher'
import NavMenu from './NavMenu'
import type { NavItem } from './NavMenu'
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

  // Item 8. Seventeen flat links filled the header and wrapped onto a second
  // row, so the page title competed with the navigation and no link had any
  // visible relationship to any other. Five groups, plus Overview kept as a
  // direct link because it is the landing page and burying it behind a menu
  // would add a click to the most common destination.
  //
  // The caste pages are still gated on `sees_caste` and Admin on the admin
  // role: a group whose every item is hidden renders nothing at all, rather
  // than an empty menu that opens onto a blank panel.
  const analyst = me?.role === 'admin' || me?.role === 'strategist'
  const groups: Array<{ id: string; key: string; items: NavItem[] }> = [
    {
      id: 'nav-results',
      key: 'groupResults',
      items: [
        { to: '/results', key: 'results' },
        { to: '/booths', key: 'booths' },
        { to: '/map', key: 'map' },
      ],
    },
    {
      id: 'nav-voters',
      key: 'groupVoters',
      items: [
        { to: '/voters', key: 'voters' },
        ...(me?.sees_caste
          ? [{ to: '/caste', key: 'caste' }, { to: '/caste-scatter', key: 'casteScatter' }]
          : []),
      ],
    },
    {
      id: 'nav-places',
      key: 'groupPlaces',
      items: [{ to: '/directory', key: 'directory' }],
    },
    {
      id: 'nav-politics',
      key: 'groupPolitics',
      items: [
        { to: '/candidates', key: 'candidates' },
        { to: '/local-politics', key: 'localPolitics' },
        { to: '/local', key: 'local' },
        { to: '/news', key: 'news' },
      ],
    },
    {
      id: 'nav-analysis',
      key: 'groupAnalysis',
      items: [
        // Transfer and Scenario are admin/strategist routes in the API; a
        // block user who followed these links got a 403 page.
        ...(analyst ? [{ to: '/transfer', key: 'transfer' }] : []),
        { to: '/factors', key: 'factors' },
        { to: '/compare', key: 'compare' },
        ...(analyst ? [{ to: '/scenario', key: 'scenario' }] : []),
      ],
    },
    ...(me?.role === 'admin'
      ? [{ id: 'nav-admin', key: 'groupAdmin', items: [{ to: '/admin', key: 'admin' }] }]
      : []),
  ].filter((group) => group.items.length > 0)

  /** Every link, flattened - the narrow-screen sheet lists them all rather
   *  than nesting menus inside a menu. */
  const links: NavItem[] = [
    { to: '/', key: 'overview' },
    ...groups.flatMap((group) => group.items),
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
            aria-label={t('nav.menu')}
            aria-expanded={navOpen}
          >
            ☰
          </button>
          {/* Item 7. This read "Giridih Monitor" with the constituency name
              beneath it, which was wrong twice over: the product serves six
              constituencies, and the one in view is the switcher's job to
              state. Two places naming the constituency meant one of them was
              always about to disagree with the other. */}
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{t('app.platform')}</div>
          </div>

          <nav className="ml-4 hidden flex-1 flex-wrap items-center gap-0.5 md:flex"
               aria-label={t('nav.label')}>
            <NavLink
              to="/"
              end
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
              {t('nav.overview')}
            </NavLink>
            {groups.map((group) => (
              <NavMenu
                key={group.id}
                id={group.id}
                label={t(`nav.${group.key}`)}
                items={group.items}
              />
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
            {/* Sign-out does not depend on /auth/me: if that failed, the user
                still needs a way out. */}
            <div className="hidden items-center gap-1.5 sm:flex">
              {me && (
                <span className="chip" title={me.role}>
                  {me.name}
                </span>
              )}
              <button className="btn px-2 py-1 text-2xs" onClick={onSignOut}>
                {t('common.signOut')}
              </button>
            </div>
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
            {/* The header's sign-out is hidden below 640 px, which is the
                width block in-charges use on their phones - so there was no
                way to sign out on a phone at all. */}
            <button className="btn ml-auto px-2.5 py-1.5 text-sm" onClick={onSignOut}>
              {t('common.signOut')}
            </button>
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
