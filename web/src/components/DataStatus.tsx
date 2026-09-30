import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import type { DataHealth } from './DataHealth'

/**
 * One line saying whether the data behind this page is there.
 *
 * The Overview used to carry the full data-health strip: eight cards, row
 * counts, and a shell command in each one. That is an operator's screen on a
 * reader's page. A strategist looking at a margin does not need
 * `python -m ingest.geocode --ac 32`, and a block in-charge cannot run it -
 * they have no shell and no credentials - so for most of the people using this
 * it was advice that could not be taken, occupying the space above the
 * analysis.
 *
 * The strip now lives on Admin under Data sources, admin only. This is what
 * replaces it: the same facts, compressed to a sentence, with no command and a
 * link for the people who can act on it.
 *
 * Deliberately not a summary score. "3 of 8 datasets loaded" tells a reader
 * nothing they can use; which dataset is missing changes what they should not
 * trust, so the missing ones are named.
 */
export default function DataStatus({
  health,
  isAdmin,
}: {
  health: DataHealth
  isAdmin: boolean
}) {
  const { t } = useTranslation()

  const parts: string[] = []

  // Results first: everything else on the page is derived from them.
  parts.push(
    health.elections_with_results > 0
      ? t('status.form20Loaded', { count: health.elections_with_results })
      : t('status.form20Missing'),
  )

  if (health.weak_crosswalks > 0) {
    parts.push(t('status.crosswalkUnreviewed', { count: health.weak_crosswalks }))
  }

  // Only the gaps are named. A list of everything that is fine is a list
  // nobody reads, and it buries the one thing that is not.
  const missing: string[] = []
  if (health.census_rows === 0) missing.push(t('health.census'))
  if (health.caste_rows === 0) missing.push(t('health.caste'))
  if (health.roll_revisions === 0) missing.push(t('health.roll'))
  if (health.local_result_rows === 0) missing.push(t('health.local'))
  if (missing.length) {
    parts.push(t('status.notLoaded', { datasets: missing.join(', ') }))
  }

  const ungeocoded = health.booths - health.booths_geocoded
  if (ungeocoded > 0) {
    parts.push(t('status.unplaced', { count: ungeocoded }))
  }

  return (
    <p
      className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1 text-2xs"
      style={{ color: 'var(--text-muted)' }}
      data-testid="data-status"
    >
      <span>{parts.join(' · ')}</span>
      {isAdmin && (
        <Link
          to="/admin"
          className="underline decoration-dotted"
          style={{ color: 'var(--text-secondary)' }}
        >
          {t('status.manage')}
        </Link>
      )}
    </p>
  )
}
