import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark' | 'system'
const KEY = 'giridih.theme'

/** Dark mode is a selected set of steps, not an automatic flip, so the toggle
 *  stamps data-theme on <html> and the token file supplies the dark values. */
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem(KEY)
      return saved === 'light' || saved === 'dark' ? saved : 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      if (theme === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  const next: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
  const label: Record<Theme, string> = { system: 'A', light: '☀', dark: '☾' }

  return (
    <button
      className="btn px-2 py-1 text-2xs"
      onClick={() => setTheme(next[theme])}
      title={`Theme: ${theme}`}
      aria-label={`Theme: ${theme}`}
    >
      {label[theme]}
    </button>
  )
}
