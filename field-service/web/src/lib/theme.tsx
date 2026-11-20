/** Theme (dark default, light toggle) and resolution of CSS-variable colours for SVG charts, which
 * take literal colours as attributes. Resolved values change when the theme changes. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type Theme = "dark" | "light";
const STORE = "em-theme";

interface ThemeCtx { theme: Theme; toggle: () => void; color: (cssVar: string) => string }
const Ctx = createContext<ThemeCtx>({ theme: "dark", toggle: () => undefined, color: (c) => c });

function initial(): Theme {
  try {
    return localStorage.getItem(STORE) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(STORE, theme); } catch { /* private mode */ }
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  const color = useMemo(() => {
    const cache = new Map<string, string>();
    return (c: string) => {
      const m = /^var\((--[\w-]+)\)$/.exec(c);
      if (!m) return c;
      if (!cache.has(m[1])) {
        cache.set(m[1], getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || c);
      }
      return cache.get(m[1])!;
    };
    // a new resolver per theme, so cached values follow the theme
  }, [theme]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Ctx.Provider value={{ theme, toggle, color }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
