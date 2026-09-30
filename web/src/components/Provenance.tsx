import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { isFixtureMode } from '../lib/api'

/**
 * How a number says where it came from, and how a blank says why.
 *
 * Three rules, applied everywhere:
 *
 *   1. A **loaded** figure names its source document and page, and the page is
 *      a link into the PDF at that page.
 *   2. A **derived** figure names its method and confidence instead, and is
 *      greyed below the 0.4 floor where it is too weak to act on.
 *   3. A **missing** figure renders as an em dash with a tooltip giving the
 *      reason - never 0, never blank, never "N/A".
 *
 * Rule 3 is the one the audit was about. Every finding in the D series was a
 * number that looked like data: a fabricated +38.3 point swing where there was
 * no prior election, a uniform 50.00% floating vote where only one poll type
 * was loaded, 0 additions everywhere because a table had no writer. A dash a
 * reader can hover is the difference between "we do not know" and a confident
 * wrong answer, and it is the whole reason `Missing` takes a mandatory reason
 * rather than an optional one.
 */

export type Confidence = number | null | undefined

/** An absent value, with the reason it is absent. The reason is required. */
export function Missing({ reason }: { reason: string }) {
  return (
    <span
      className="cursor-help border-b border-dotted"
      style={{ color: 'var(--text-muted)', borderColor: 'var(--text-muted)' }}
      title={reason}
      aria-label={`Not available: ${reason}`}
      data-testid="missing"
    >
      —
    </span>
  )
}

/**
 * A value, or a dash with a reason.
 *
 * The signature forces the caller to have a reason to hand: there is no way to
 * render a blank without one, which is deliberate. Where a page cannot say why
 * a figure is missing, that is a gap in the API response, not something to
 * paper over in the view.
 */
export function Value<T>({
  value,
  reason,
  render,
}: {
  value: T | null | undefined
  reason: string
  render: (v: T) => React.ReactNode
}) {
  if (value === null || value === undefined) return <Missing reason={reason} />
  return <>{render(value)}</>
}

/**
 * Source document and page for a loaded figure, as a link into the PDF.
 *
 * Three states, and the two that are not "a real page" used to be rendered as
 * if they were:
 *
 *   * **No document.** A dash with a reason, as everywhere else.
 *   * **A document but no page.** This rendered `p?` and still linked into the
 *     PDF. `p?` is not a page number, it is the absence of one dressed as a
 *     value, and the link invited a reader to go and check a page that was
 *     never recorded. Now a dash with a tooltip naming the document, and no
 *     link - there is nothing specific to open.
 *   * **Fixture data.** In fixture mode the "document" is invented, so a link
 *     to `/raw/...` is a 404 at best and, worse, presents made-up provenance as
 *     real. Labelled as a fixture instead, and not a link.
 */
export function SourceLink({
  doc,
  page,
  compact = false,
}: {
  doc?: string | null
  page?: number | null
  compact?: boolean
}) {
  const { t } = useTranslation()
  if (!doc) {
    return <Missing reason={t('prov.noSource')} />
  }

  // Fixtures are not evidence. Say so rather than linking to a file that either
  // does not exist or, on a machine that happens to have a real raw/ tree,
  // is not where this number came from.
  if (isFixtureMode()) {
    return (
      <span
        className="text-2xs"
        style={{ color: 'var(--text-muted)' }}
        title={t('prov.fixtureSource', { doc })}
      >
        {t('prov.fixtureLabel')}
      </span>
    )
  }

  if (!page) {
    return (
      <span
        className="text-2xs"
        style={{ color: 'var(--text-muted)' }}
        title={t('prov.pageUnknown', { doc })}
      >
        —
      </span>
    )
  }

  // The raw/ tree is served read-only by nginx at /raw. #page= is honoured by
  // every embedded PDF viewer, so a figure is one click from the page it was
  // read off.
  const href = `/raw/${doc}#page=${page}`
  return (
    <a
      className="text-2xs underline decoration-dotted"
      style={{ color: 'var(--text-muted)' }}
      href={href}
      target="_blank"
      rel="noreferrer"
      title={t('prov.openSource', { doc, page })}
    >
      {compact ? `p${page}` : `${doc} p${page}`}
    </a>
  )
}

/** The confidence floor below which an estimate is too weak to act on. */
export const CONFIDENCE_FLOOR = 0.4

export function confidenceBand(c: Confidence): 'none' | 'low' | 'medium' | 'high' {
  if (c === null || c === undefined) return 'none'
  if (c < CONFIDENCE_FLOOR) return 'low'
  if (c < 0.7) return 'medium'
  return 'high'
}

/**
 * An estimated figure: the value, the method, and the confidence.
 *
 * Below the floor the value is greyed and labelled rather than hidden. Hiding
 * it would leave a reader guessing; showing it plainly would invite them to use
 * it. The audit was explicit that the frontend must not receive bare numbers
 * for these, and this is the component that keeps that true.
 */
export function Estimate({
  value,
  confidence,
  method,
  reason,
  render,
}: {
  value: number | null | undefined
  confidence: Confidence
  method?: string | null
  reason: string
  render: (v: number) => React.ReactNode
}) {
  const { t } = useTranslation()
  if (value === null || value === undefined) return <Missing reason={reason} />

  const band = confidenceBand(confidence)
  const tooLow = band === 'low'
  const title = [
    t('prov.estimate'),
    method ? `${t('prov.method')}: ${method}` : null,
    confidence !== null && confidence !== undefined
      ? `${t('prov.confidence')}: ${confidence.toFixed(2)}`
      : t('prov.noConfidence'),
    tooLow ? t('prov.belowFloor', { floor: CONFIDENCE_FLOOR }) : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <span
      className="cursor-help"
      style={{ color: tooLow ? 'var(--text-muted)' : undefined }}
      title={title}
      data-testid="estimate"
    >
      {render(value)}
      <ConfidenceDot confidence={confidence} />
      {tooLow && (
        <span className="ml-1 text-3xs">{t('caste.insufficient')}</span>
      )}
    </span>
  )
}

/** A small mark carrying the confidence band, so a table scans quickly. */
export function ConfidenceDot({ confidence }: { confidence: Confidence }) {
  const { t } = useTranslation()
  const band = confidenceBand(confidence)
  const colour = {
    none: 'var(--text-muted)',
    low: 'var(--status-critical, #d03b3b)',
    medium: 'var(--status-warn, #c98500)',
    high: 'var(--status-ok, #1a6b39)',
  }[band]
  return (
    <span
      aria-label={t('prov.confidenceBand', { band })}
      title={
        confidence === null || confidence === undefined
          ? t('prov.noConfidence')
          : `${t('prov.confidence')}: ${confidence.toFixed(2)}`
      }
      className="ml-1 inline-block h-1.5 w-1.5 rounded-full align-middle"
      style={{ background: colour }}
    />
  )
}

/**
 * The fixture banner. Shown whenever a response carries a `fixture` field, so
 * nobody can mistake a reviewable page for a loaded one.
 */
export function FixtureBanner({ note }: { note?: string | null }) {
  if (!note) return null
  return (
    <div
      className="rounded px-3 py-2 text-2xs"
      style={{
        background: 'var(--status-warn-bg, var(--surface-2))',
        color: 'var(--status-warn, var(--text-secondary))',
        border: '1px solid var(--status-warn, var(--text-muted))',
      }}
      role="status"
      data-testid="fixture-banner"
    >
      {note}
    </div>
  )
}

/**
 * Copy a shell command to the clipboard.
 *
 * The commands in the data-health strip exist so an operator can run them, and
 * retyping `python -m ingest.load_form20 --ac 32 --doc ...` from a wrapped
 * code block is where typos come from. Falls back to selecting the text when
 * the Clipboard API is unavailable, which is any page not served over HTTPS or
 * localhost - including, quite possibly, the one this is deployed on.
 */
export function CopyButton({ value }: { value: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // No clipboard permission. Say nothing rather than claiming success:
      // a button that reports "copied" over an empty clipboard is worse than
      // one that visibly does nothing.
      setCopied(false)
    }
  }

  return (
    <button
      className="btn shrink-0 px-1 py-0 text-3xs"
      onClick={() => void copy()}
      aria-label={t('common.copyCommand')}
      title={copied ? t('common.copied') : t('common.copyCommand')}
    >
      {copied ? '✔' : '⧉'}
    </button>
  )
}


/**
 * One cell of the data-health strip: whether a dataset is loaded, partial or
 * missing, and the command that fills it.
 */
export function HealthCell({
  label,
  state,
  detail,
  command,
}: {
  label: string
  state: 'loaded' | 'partial' | 'missing'
  detail?: string | null
  command?: string | null
}) {
  const { t } = useTranslation()
  const colour = {
    loaded: 'var(--status-ok, #1a6b39)',
    partial: 'var(--status-warn, #c98500)',
    missing: 'var(--status-critical, #d03b3b)',
  }[state]
  const mark = { loaded: '✔', partial: '~', missing: '✖' }[state]

  return (
    <div className="flex flex-col gap-0.5 rounded px-2 py-1.5"
         style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-baseline gap-1.5">
        <span aria-hidden style={{ color: colour }}>{mark}</span>
        <span className="text-2xs font-medium">{label}</span>
      </div>
      <div className="text-3xs" style={{ color: 'var(--text-secondary)' }}>
        {detail ?? t(`health.${state}`)}
      </div>
      {state !== 'loaded' && command && (
        // Item 5: this was `overflow-x-auto whitespace-nowrap`, which put a
        // horizontal scrollbar inside a card in a grid - the one place a
        // scrollbar is least expected and hardest to reach. The command wraps
        // now, on whitespace and as a last resort mid-token, and there is a
        // copy button because the reason it is on screen at all is for someone
        // to run it.
        <div className="mt-0.5 flex items-start gap-1">
          <code
            className="min-w-0 flex-1 whitespace-pre-wrap break-all text-3xs"
            style={{ color: 'var(--text-muted)' }}
            title={t('health.runThis')}
          >
            {command}
          </code>
          <CopyButton value={command} />
        </div>
      )}
    </div>
  )
}
