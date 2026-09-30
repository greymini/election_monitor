import { useTranslation } from 'react-i18next'

import { partyColor, token } from '../lib/tokens'

const STEPS = ['--div-a5', '--div-a4', '--div-a3', '--div-a2', '--div-a1',
               '--div-mid',
               '--div-b1', '--div-b2', '--div-b3', '--div-b4', '--div-b5']

interface Props {
  saturateAt?: number
  leftParty?: string
  rightParty?: string
  title?: string
}

/**
 * The margin scale is blue-to-red rather than the parties' own green and
 * saffron. Those two are indistinguishable under red-green colour blindness
 * once a diverging ramp forces them to matched lightness, so the ramp uses the
 * validated blue/red pair and this legend carries the party colours as chips at
 * each end - identity by label and chip, magnitude by ramp.
 */
export default function DivergingLegend({
  saturateAt = 20, leftParty = 'JMM', rightParty = 'BJP', title,
}: Props) {
  const { t } = useTranslation()
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
             aria-label={`${leftParty} lead to ${rightParty} lead, saturating at ${saturateAt} points`}>
          {STEPS.map((step) => (
            <span key={step} className="flex-1" style={{ background: token(step) }} />
          ))}
        </div>
        <span className="inline-flex items-center gap-1 text-2xs font-medium">
          {rightParty}
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: partyColor(rightParty) }} />
        </span>
      </div>
      <div className="tnum mt-0.5 flex justify-between text-2xs" style={{ color: 'var(--text-muted)' }}>
        <span>+{saturateAt}</span>
        <span>0</span>
        <span>+{saturateAt}</span>
      </div>
    </div>
  )
}
