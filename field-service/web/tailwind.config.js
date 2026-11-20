/** Tailwind reads every colour, radius and font from the CSS variables in src/styles/tokens.css,
 * so the theme lives in one place and maps one-to-one onto the core's tokens at merge. */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        page: "var(--em-page)",
        surface: "var(--em-surface)",
        raised: "var(--em-raised)",
        line: "var(--em-border)",
        divider: "var(--em-divider)",
        grid: "var(--em-grid)",
        ink: "var(--em-text)",
        "ink-2": "var(--em-text-2)",
        muted: "var(--em-muted)",
        accent: "var(--em-accent)",
        "accent-ink": "var(--em-accent-ink)",
        ok: "var(--em-ok)",
        warn: "var(--em-warn)",
        serious: "var(--em-serious)",
        bad: "var(--em-bad)",
      },
      borderRadius: { card: "var(--em-radius-card)", ctl: "var(--em-radius-ctl)", chip: "var(--em-radius-chip)" },
      fontFamily: { sans: "var(--em-font)", mono: "var(--em-font-mono)" },
      fontSize: {
        kpi: ["var(--em-size-kpi)", { lineHeight: "1.15", fontWeight: "700" }],
        title: ["var(--em-size-title)", { lineHeight: "1.25", fontWeight: "700" }],
        panel: ["var(--em-size-panel)", { lineHeight: "1.3", fontWeight: "700" }],
        body: ["var(--em-size-body)", { lineHeight: "1.45" }],
        note: ["var(--em-size-note)", { lineHeight: "1.45" }],
        label: ["var(--em-size-label)", { lineHeight: "1.3", letterSpacing: "0.04em" }],
      },
      spacing: { page: "var(--em-space-page)", card: "var(--em-space-card)", gap: "var(--em-space-gap)" },
    },
  },
  plugins: [],
};
