import { token } from '../lib/tokens'

const STEPS = ['--seq-1', '--seq-2', '--seq-3', '--seq-4', '--seq-5', '--seq-6', '--seq-7']

/** One hue, light to dark: magnitude only, no polarity. */
export default function SequentialLegend({ title, max, unit = '%' }: {
  title: string; max: number; unit?: string
}) {
  return (
    <div className="card px-3 py-2">
      <div className="text-2xs font-medium uppercase tracking-wide"
           style={{ color: 'var(--text-muted)' }}>
        {title}
      </div>
      <div className="mt-1.5 flex h-3 overflow-hidden rounded" role="img"
           aria-label={`0 to ${max}${unit}`}>
        {STEPS.map((step) => (
          <span key={step} className="flex-1" style={{ background: token(step) }} />
        ))}
      </div>
      <div className="tnum mt-0.5 flex justify-between text-2xs" style={{ color: 'var(--text-muted)' }}>
        <span>0</span>
        <span>{max}{unit}</span>
      </div>
    </div>
  )
}
