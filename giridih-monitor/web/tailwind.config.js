/** Tokens mirror src/styles/tokens.css so Tailwind classes and chart code
 *  draw from the same validated palette. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: 'var(--surface-1)',
        plane: 'var(--plane)',
        ink: {
          DEFAULT: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        },
        hairline: 'var(--gridline)',
        baseline: 'var(--baseline)',
        party: {
          jmm: 'var(--party-jmm)',
          bjp: 'var(--party-bjp)',
          jlkm: 'var(--party-jlkm)',
          ajsu: 'var(--party-ajsu)',
          inc: 'var(--party-inc)',
          other: 'var(--party-other)',
        },
        status: {
          good: 'var(--status-good)',
          warning: 'var(--status-warning)',
          serious: 'var(--status-serious)',
          critical: 'var(--status-critical)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'Noto Sans Devanagari', 'system-ui', 'sans-serif'],
        hi: ['Noto Sans Devanagari', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: { '2xs': ['0.6875rem', { lineHeight: '1rem' }] },
    },
  },
  plugins: [],
}
