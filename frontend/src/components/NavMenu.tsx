import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

export interface NavItem {
  to: string
  key: string
}

/**
 * One grouped navigation menu.
 *
 * Seventeen flat links filled the header and wrapped onto a second row, so the
 * page title competed with the navigation for the top of the screen and no link
 * had any apparent relationship to any other. They are five groups now.
 *
 * **Keyboard access is the part worth getting right,** because a menu is where
 * it is usually lost. This is a disclosure button plus a list, not a
 * `role="menu"` widget: the ARIA menu pattern demands full arrow-key
 * navigation, type-ahead and focus management, and a half-implemented
 * `role="menu"` is worse for a screen reader than honest buttons and links,
 * because it promises interactions that are not there.
 *
 * So: `aria-expanded` and `aria-controls` on the button, a plain list of
 * `NavLink`s inside, Escape to close and return focus to the button, a click
 * outside to close, and arrow keys as a convenience rather than a contract.
 * Tab moves through the links, which is what a keyboard user will expect from a
 * button labelled with a group name.
 */
export default function NavMenu({
  id,
  label,
  items,
}: {
  id: string
  label: string
  items: NavItem[]
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const wrapper = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const location = useLocation()

  // A group is "current" when the route is inside it, so the header still shows
  // where you are once the links are hidden behind a button.
  const active = items.some(
    (item) => location.pathname === item.to || location.pathname.startsWith(`${item.to}/`),
  )

  // Navigating closes the menu. Without this the panel stays open over the page
  // it just navigated to.
  useEffect(() => setOpen(false), [location.pathname])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        button.current?.focus()
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="relative" ref={wrapper}>
      <button
        ref={button}
        id={`${id}-button`}
        className={`rounded-lg px-2.5 py-1.5 text-sm transition-colors ${active ? 'font-semibold' : ''}`}
        style={{
          background: active ? 'var(--surface-2)' : 'transparent',
          color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        }}
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && !open) {
            event.preventDefault()
            setOpen(true)
          }
        }}
      >
        {label}
        <span aria-hidden className="ml-1 text-3xs">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div
          id={`${id}-panel`}
          aria-labelledby={`${id}-button`}
          className="absolute left-0 top-full z-40 mt-1 min-w-[12rem] rounded-lg border py-1 shadow-lg"
          style={{ background: 'var(--surface-1)', borderColor: 'var(--gridline)' }}
        >
          <ul className="flex flex-col">
            {items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  className="block px-3 py-1.5 text-sm"
                  style={({ isActive }) => ({
                    color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontWeight: isActive ? 600 : 400,
                    background: isActive ? 'var(--surface-2)' : 'transparent',
                  })}
                >
                  {t(`nav.${item.key}`)}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
