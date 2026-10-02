import { useTranslation } from 'react-i18next'

import { partyCode, UNRECORDED_PARTY } from '../lib/results'
import { partyColor } from '../lib/tokens'

/** Identity is never carried by colour alone: the chip is a dot plus the
 *  party's name, so it still reads in greyscale and under forced colours.
 *
 *  Accepts a contestant key too ("UNK:Name", "IND:Name"): the views rank those
 *  per candidate, and the chip shows the party part. A candidate whose party
 *  the source does not record reads "party not in source", never "UNK". */
export default function PartyChip({ abbr, size = 'sm' }: { abbr?: string | null; size?: 'sm' | 'md' }) {
  const { t } = useTranslation()
  const code = partyCode(abbr)
  if (!code) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const unrecorded = code === UNRECORDED_PARTY
  return (
    <span className={`inline-flex items-center gap-1 font-medium ${size === 'sm' ? 'text-2xs' : 'text-sm'}`}
          title={unrecorded ? t('results.partyNotInSourceHelp') : undefined}>
      <span
        aria-hidden
        className="inline-block shrink-0 rounded-full"
        style={{
          width: size === 'sm' ? 8 : 10,
          height: size === 'sm' ? 8 : 10,
          background: partyColor(code),
          boxShadow: '0 0 0 2px var(--surface-1)',
        }}
      />
      {unrecorded
        ? <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>{t('results.partyNotInSource')}</span>
        : code}
    </span>
  )
}
