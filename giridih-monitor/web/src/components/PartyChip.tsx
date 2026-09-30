import { partyColor } from '../lib/tokens'

/** Identity is never carried by colour alone: the chip is a dot plus the
 *  party's name, so it still reads in greyscale and under forced colours. */
export default function PartyChip({ abbr, size = 'sm' }: { abbr?: string | null; size?: 'sm' | 'md' }) {
  if (!abbr) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  return (
    <span className={`inline-flex items-center gap-1 font-medium ${size === 'sm' ? 'text-2xs' : 'text-sm'}`}>
      <span
        aria-hidden
        className="inline-block shrink-0 rounded-full"
        style={{
          width: size === 'sm' ? 8 : 10,
          height: size === 'sm' ? 8 : 10,
          background: partyColor(abbr),
          boxShadow: '0 0 0 2px var(--surface-1)',
        }}
      />
      {abbr}
    </span>
  )
}
