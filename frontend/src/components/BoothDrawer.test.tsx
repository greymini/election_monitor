import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import BoothDrawer from './BoothDrawer'
import ErrorBoundary from './ErrorBoundary'
import liveCard from '../test/data/live-booth-card.json'
import { mockServer } from '../test/server'
import { fakeAc, renderWithProviders } from '../test/render'

/**
 * The booth drawer against the card the real API returns.
 *
 * `live-booth-card.json` is a verbatim response from `scripts/dev_stack.py`
 * (GET /acs/32/booths/32-B0001/card). Before the fix the backend 500'd, and
 * its earlier shape (`roll_snapshots`, `crosswalk[].label`, no
 * `priority.inputs_used`) would have thrown in the Voters and Sources tabs.
 */
const TABS = ['Results', 'Voters', 'Community', 'News', 'Ground', 'Sources']

function renderDrawer(card: unknown) {
  mockServer({ '/acs/32/booths/32-B0001/card': { body: card } })
  return renderWithProviders(
    <ErrorBoundary>
      <BoothDrawer boothUid="32-B0001" ac={fakeAc(32)} onClose={() => {}} />
    </ErrorBoundary>,
  )
}

describe('BoothDrawer', () => {
  it('renders every tab of a live card without crashing', async () => {
    renderDrawer(liveCard)
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/32-B0001/)).toBeInTheDocument()

    for (const name of TABS) {
      await userEvent.click(within(dialog).getByRole('button', { name }))
      expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
    }
  })

  it('names the dialog with the booth, not a raw translation key', async () => {
    renderDrawer(liveCard)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.getAttribute('aria-label')).toContain('32-B0001')
    expect(dialog.getAttribute('aria-label')).not.toContain('card.heading')
  })

  it('shows which priority inputs were used', async () => {
    renderDrawer(liveCard)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Sources' }))
    expect(await within(dialog).findByText(/closeness, volatility/)).toBeInTheDocument()
  })

  it('survives a card with no roll, priority or new-voter data', async () => {
    renderDrawer({
      ...liveCard, roll: [], roll_changes: [], crosswalk: [],
      priority: null, new_voters: null,
    })
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('button', { name: 'Results' })
    for (const name of TABS) {
      await userEvent.click(within(dialog).getByRole('button', { name }))
      expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
    }
  })
})
