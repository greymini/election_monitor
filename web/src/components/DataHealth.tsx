import { useTranslation } from 'react-i18next'

import { HealthCell } from './Provenance'

/**
 * The data-health strip (spec §7.1): per dataset, loaded / partial / missing,
 * with the command that fills the gap.
 *
 * This is the most useful thing on the overview for five of the six
 * constituencies, because for five of them the honest answer to almost every
 * question is "not loaded". Without this strip, an empty Results page and a
 * broken Results page look identical; with it, the page says which dataset is
 * absent and what to run.
 *
 * The rule each cell follows: a count of zero is **missing**, not loaded-with-
 * nothing. That distinction is the whole point - the audit's D-series findings
 * were all numbers that read as data when they were really absence.
 */

export interface DataHealth {
  booths: number
  booths_geocoded: number
  ps_list_rows: number
  elections_with_results: number
  open_reviews: number
  weak_crosswalks: number
  crosswalk_rows: number
  latest_roll: string | null
  roll_revisions: number
  caste_rows: number
  census_rows: number
  local_result_rows: number
  source_docs: number
}

type State = 'loaded' | 'partial' | 'missing'

function state(loaded: boolean, partial = false): State {
  if (!loaded) return 'missing'
  return partial ? 'partial' : 'loaded'
}

export default function DataHealthStrip({
  health,
  acNumber,
}: {
  health: DataHealth
  acNumber: number
}) {
  const { t } = useTranslation()
  const ac = acNumber

  const cells: Array<{
    label: string
    state: State
    detail: string | null
    command: string | null
  }> = [
    {
      label: t('health.psList'),
      state: state(health.ps_list_rows > 0),
      detail: health.ps_list_rows > 0
        ? t('health.rows', { n: health.ps_list_rows })
        : null,
      command: `python -m ingest.parse_pslist <pdf> --election VS-2024 --load --anchor`,
    },
    {
      label: t('health.form20'),
      state: state(health.elections_with_results > 0, health.elections_with_results < 2),
      detail: health.elections_with_results > 0
        ? t('health.electionsLoaded', { count: health.elections_with_results })
        : null,
      command: `python -m ingest.parse_form20 <pdf> --election VS-2024 --load`,
    },
    {
      label: t('health.crosswalk'),
      state: state(health.crosswalk_rows > 0, health.weak_crosswalks > 0),
      // A weak crosswalk is the specific thing that makes a multi-year
      // comparison provisional, so it is named rather than counted silently.
      detail: health.crosswalk_rows > 0
        ? health.weak_crosswalks > 0
          ? t('health.weakCrosswalks', { n: health.weak_crosswalks })
          : t('health.rows', { n: health.crosswalk_rows })
        : null,
      command: `python -m ingest.crosswalk --ac ${ac} --election VS-2019 --apply`,
    },
    {
      label: t('health.rolls'),
      state: state(health.roll_revisions > 0, health.roll_revisions < 2),
      detail: health.latest_roll
        ? t('health.latestRoll', { date: health.latest_roll })
        : null,
      command: `python -m ingest.parse_roll <pdf> --revision 2026-07 --date 2026-07-01 --load`,
    },
    {
      label: t('health.geocode'),
      state: state(
        health.booths_geocoded > 0,
        health.booths_geocoded < health.booths,
      ),
      detail: health.booths > 0
        ? t('health.geocoded', { done: health.booths_geocoded, total: health.booths })
        : null,
      command: `python -m ingest.geocode`,
    },
    {
      label: t('health.caste'),
      state: state(health.caste_rows > 0),
      detail: health.caste_rows > 0
        ? t('health.rows', { n: health.caste_rows })
        : null,
      command: `python -m analytics.caste_estimate`,
    },
    {
      label: t('health.census'),
      state: state(health.census_rows > 0),
      detail: null,
      command: `python -m ingest.load_csv demography <csv> --ac ${ac}`,
    },
    {
      label: t('health.local'),
      state: state(health.local_result_rows > 0),
      detail: health.local_result_rows > 0
        ? t('health.rows', { n: health.local_result_rows })
        : null,
      command: `python -m ingest.fetch_sec --ac ${ac} --election PANCHAYAT-2022 --load-csv <csv>`,
    },
  ]

  const missing = cells.filter((c) => c.state === 'missing').length

  return (
    <section className="card px-4 py-3" data-testid="data-health">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">{t('health.heading')}</h2>
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {missing === 0
            ? t('health.allLoaded')
            : t('health.someMissing', { count: missing })}
        </span>
      </div>

      <div className="mt-2 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
        {cells.map((c) => (
          <HealthCell
            key={c.label}
            label={c.label}
            state={c.state}
            detail={c.detail}
            command={c.command}
          />
        ))}
      </div>

      {health.open_reviews > 0 && (
        <p className="mt-2 text-2xs" style={{ color: 'var(--status-warn, var(--text-muted))' }}>
          {t('health.openReviews', { count: health.open_reviews })}
        </p>
      )}

      <p className="mt-1.5 text-3xs" style={{ color: 'var(--text-muted)' }}>
        {t('health.note')}
      </p>
    </section>
  )
}
