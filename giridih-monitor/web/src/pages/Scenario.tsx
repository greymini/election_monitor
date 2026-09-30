import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import StatTile from '../components/StatTile'
import { ErrorState } from '../components/States'
import { api } from '../lib/api'
import { num, signedNum } from '../lib/format'
import { partyColor, token } from '../lib/tokens'

interface Result {
  booths: number
  votes: Record<string, number>
  margin: { point: number; p10: number; p50: number; p90: number }
  winner: string
  total_votes: number
  high_variance_booths: Array<[string, number]>
  draws: number
  disclaimer: string
}

export default function Scenario() {
  const { t } = useTranslation()
  const [turnout, setTurnout] = useState(1.0)
  const [sympathy, setSympathy] = useState(0)
  const [jlkmToBjp, setJlkmToBjp] = useState(0.5)
  const [newVoterTurnout, setNewVoterTurnout] = useState(0.6)

  const run = useMutation<Result>({
    mutationFn: () =>
      api.post('/scenario', {
        turnout_multiplier: turnout,
        sympathy_swing: sympathy,
        jlkm_to_bjp: jlkmToBjp,
        new_voter_turnout: newVoterTurnout,
        draws: 500,
      }),
  })

  const result = run.data
  const span = result ? result.margin.p90 - result.margin.p10 : 0
  // The projection is only meaningful if the range stays on one side of zero.
  const decisive = result ? Math.sign(result.margin.p10) === Math.sign(result.margin.p90) : false

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">{t('scenario.heading')}</h1>

      <section className="card px-4 py-3">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Slider label={t('scenario.turnout')} value={turnout} min={0.8} max={1.2} step={0.01}
                  onChange={setTurnout} format={(v) => `${(v * 100).toFixed(0)}%`} />
          <Slider label={t('scenario.sympathy')} value={sympathy} min={-0.15} max={0.15} step={0.01}
                  onChange={setSympathy}
                  format={(v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(0)}% BJP→JMM`} />
          <Slider label={t('scenario.jlkmToBjp')} value={jlkmToBjp} min={0} max={1} step={0.05}
                  onChange={setJlkmToBjp} format={(v) => `${(v * 100).toFixed(0)}% to BJP`} />
          <Slider label={t('scenario.newVoterTurnout')} value={newVoterTurnout} min={0} max={1}
                  step={0.05} onChange={setNewVoterTurnout}
                  format={(v) => `${(v * 100).toFixed(0)}%`} />
        </div>
        <button className="btn btn-primary mt-4" onClick={() => run.mutate()}
                disabled={run.isPending}>
          {run.isPending ? t('common.loading') : t('scenario.run')}
        </button>
      </section>

      {run.isError && <ErrorState error={run.error} />}

      {result && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label={t('scenario.projectedMargin')}
              value={signedNum(result.margin.point)}
              sub={`${result.winner} ahead · ${num(result.booths)} booths`}
              accent={partyColor(result.winner)}
            />
            <StatTile label="P10 — P90" value={`${signedNum(result.margin.p10)} … ${signedNum(result.margin.p90)}`}
                      sub={`${result.draws} draws, ±5% noise`} />
            <StatTile label="Range width" value={num(span)}
                      sub="Wider than the margin means the result is inside the noise"
                      status={decisive ? 'good' : 'warning'}
                      statusText={decisive ? 'Range stays one side of zero' : 'Range crosses zero — too close to call'} />
            <StatTile label={t('common.votes')} value={num(result.total_votes)} />
          </div>

          <section className="card px-4 py-3">
            <h2 className="text-sm font-semibold">{t('common.votes')}</h2>
            <ul className="mt-2 space-y-1.5">
              {Object.entries(result.votes)
                .filter(([, v]) => v > 0)
                .map(([party, votes]) => {
                  const share = result.total_votes ? (votes / result.total_votes) * 100 : 0
                  return (
                    <li key={party}>
                      <div className="flex items-baseline justify-between text-2xs">
                        <span className="font-medium">{party}</span>
                        <span className="tnum" style={{ color: 'var(--text-secondary)' }}>
                          {num(votes)} · {share.toFixed(1)}%
                        </span>
                      </div>
                      <div className="mt-0.5 h-2 overflow-hidden rounded"
                           style={{ background: 'var(--surface-2)' }}>
                        <div className="h-full rounded"
                             style={{ width: `${share}%`, background: partyColor(party) }} />
                      </div>
                    </li>
                  )
                })}
            </ul>
          </section>

          {result.high_variance_booths.length > 0 && (
            <section className="card px-4 py-3">
              <h2 className="text-sm font-semibold">Booths where the outcome varies most</h2>
              <p className="mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                Standard deviation of this booth's margin across {result.draws} draws. These are
                where the seat is actually decided under your assumptions.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {result.high_variance_booths.map(([uid, sd]) => (
                  <span key={uid} className="chip tnum"
                        style={{ background: token('--surface-2') }}>
                    {uid} · ±{sd.toFixed(1)}
                  </span>
                ))}
              </div>
            </section>
          )}

          <p className="text-2xs" style={{ color: 'var(--status-warning)' }}>
            ! {result.disclaimer}
          </p>
        </>
      )}

      {!result && (
        <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {t('scenario.disclaimer')}
        </p>
      )}
    </div>
  )
}

function Slider({ label, value, min, max, step, onChange, format }: {
  label: string; value: number; min: number; max: number; step: number
  onChange: (v: number) => void; format: (v: number) => string
}) {
  return (
    <label className="block">
      <span className="text-2xs font-medium uppercase tracking-wide"
            style={{ color: 'var(--text-muted)' }}>
        {label}
      </span>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))} className="mt-1 w-full" />
      <span className="tnum text-sm">{format(value)}</span>
    </label>
  )
}
