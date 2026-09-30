import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

type Theme = 'light' | 'dark' | 'system'
const KEY = 'giridih.theme'

/** Dark mode is a selected set of steps, not an automatic flip, so the toggle
 *  stamps data-theme on <html> and the token file supplies the dark values. */
export default function ThemeToggle() {
  const { t } = useTranslation()
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
  // 'A' was the glyph for `system`, which means nothing - it is not an
  // abbreviation of anything a reader would guess, and the accessible name said
  // only "Theme: system", so a screen reader announced a state and no action.
  // The half-filled circle is the conventional auto/system mark.
  const glyph: Record<Theme, string> = { system: '◐', light: '☀', dark: '☾' }

  // The name describes what pressing it *does*, which is what a button's
  // accessible name is for, and it is translated like everything else.
  const name = t('theme.switchTo', { mode: t(`theme.${next[theme]}`) })

  return (
    <button
      className="btn px-2 py-1 text-2xs"
      onClick={() => setTheme(next[theme])}
      title={`${t(`theme.current`, { mode: t(`theme.${theme}`) })} — ${name}`}
      aria-label={name}
    >
      <span aria-hidden>{glyph[theme]}</span>
    </button>
  )
}
