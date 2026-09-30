import { useTranslation } from 'react-i18next'

import type { AcState } from '../lib/ac'

/**
 * The constituency switcher, in the header on every page.
 *
 * The "unverified" badge is not decoration. Five of the six constituencies are
 * seeded from secondary sources, and the spec is explicit that no figure from
 * them should be used before a human has checked it against ECI/CEO
 * publications. Somebody reading a margin needs to know which kind of number
 * they are looking at without going to find out.
 */
export default function AcSwitcher({ state }: { state: AcState }) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'

  if (state.all.length === 0) {
    return null
  }

  return (
    <div className="flex items-center gap-1.5">
      <label className="sr-only" htmlFor="ac-switcher">
        {t('ac.label')}
      </label>
      <select
        id="ac-switcher"
        className="select text-2xs"
        value={state.acNumber ?? ''}
        onChange={(e) => state.setAc(Number(e.target.value))}
        aria-label={t('ac.label')}
      >
        {state.all.map((ac) => (
          <option key={ac.ac_number} value={ac.ac_number}>
            {`${ac.ac_number} · ${hi ? ac.name_hi : ac.name_en}`}
            {ac.reservation !== 'GEN' ? ` (${ac.reservation})` : ''}
          </option>
        ))}
      </select>

      {state.ac && !state.ac.verified && (
        <span
          className="rounded px-1.5 py-0.5 text-3xs font-medium"
          style={{
            background: 'var(--status-warn-bg, var(--surface-2))',
            color: 'var(--status-warn, var(--text-secondary))',
          }}
          title={t('ac.unverifiedHelp')}
        >
          {t('ac.unverified')}
        </span>
      )}
    </div>
  )
}
