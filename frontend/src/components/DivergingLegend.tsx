import { useTranslation } from 'react-i18next'

import { divergingColor, divergingPartySteps, partyColor } from '../lib/tokens'

interface Props {
  saturateAt?: number
  leftParty?: string
  rightParty?: string
  title?: string
  /** A non-party scale (e.g. LS-to-VS change): the neutral blue/red ramp that
   *  `divergingColor` paints, with these end labels instead of party chips.
   *  The Transfer page coloured its cells with that ramp while this legend
   *  showed the JMM/BJP party ramp, so the key did not match the table. */
  neutral?: { left: string; right: string }
}

/**
 * The margin scale, in the contest pair's own colours.
 *
 * The ramp swatches come from `divergingPartySteps`, which is the same function
 * the markers use, so a booth's colour and the legend agree by construction
 * rather than by two palettes being kept in step by hand. That was the point of
 * the fix: the ramp ends are now the colours of the chips beside them.
 *
 * **The colour-blindness concern this replaces, addressed rather than dropped.**
 * This legend previously used a validated blue/red ramp, on the argument that
 * JMM green and BJP saffron are hard to separate under red-green colour
 * blindness. That is true when a ramp forces both arms to matched lightness. It
 * is not true of these two: the green sits near 0.13 relative luminance and the
 * saffron near 0.42, so the arms differ in lightness by about a factor of three
 * and stay separable with no hue information at all. `luminanceGap` in
 * `lib/tokens.ts` exists so a test can hold that, and the numeric scale below
 * means the magnitude is readable without any colour.
 */
export default function DivergingLegend({
  saturateAt = 20, leftParty = 'JMM', rightParty = 'BJP', title, neutral,
}: Props) {
  const { t } = useTranslation()
  const steps = neutral
    ? Array.from({ length: 11 }, (_, i) => divergingColor(-saturateAt + (i * saturateAt) / 5, saturateAt))
    : divergingPartySteps(saturateAt, leftParty, rightParty)
  if (neutral) {
    return (
      <div className="card px-3 py-2">
        <div className="text-2xs font-medium uppercase tracking-wide"
             style={{ color: 'var(--text-muted)' }}>
          {title}
        </div>
        <div className="mt-1.5 flex h-3 overflow-hidden rounded" role="img"
             aria-label={`${neutral.left} – ${neutral.right}`}>
          {steps.map((colour, index) => (
            <span key={index} className="flex-1" style={{ background: colour }} />
          ))}
        </div>
        <div className="tnum mt-0.5 flex justify-between gap-2 text-2xs"
             style={{ color: 'var(--text-muted)' }}>
          <span>{neutral.left} +{saturateAt}</span>
          <span>0</span>
          <span>{neutral.right} +{saturateAt}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="card px-3 py-2">
      <div className="text-2xs font-medium uppercase tracking-wide"
           style={{ color: 'var(--text-muted)' }}>
        {title ?? t('map.legendMargin')}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="inline-flex items-center gap-1 text-2xs font-medium">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: partyColor(leftParty) }} />
          {leftParty}
        </span>
        <div className="flex h-3 flex-1 overflow-hidden rounded" role="img"
             aria-label={t('map.legendRamp', { left: leftParty, right: rightParty, points: saturateAt })}>
          {steps.map((colour, index) => (
            <span key={index} className="flex-1" style={{ background: colour }} />
          ))}
        </div>
        <span className="inline-flex items-center gap-1 text-2xs font-medium">
          {rightParty}
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: partyColor(rightParty) }} />
        </span>
      </div>
      {/* Both ends read "+30%" with no party, so the scale said the margin
          could be +30 at either end and nothing said which party that favoured.
          Each end now carries its party and its sign. */}
      <div className="tnum mt-0.5 flex justify-between gap-2 text-2xs"
           style={{ color: 'var(--text-muted)' }}>
        <span>{leftParty} +{saturateAt}%</span>
        <span>0</span>
        <span>{rightParty} +{saturateAt}%</span>
      </div>
    </div>
  )
}
