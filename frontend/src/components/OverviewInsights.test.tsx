import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import OverviewInsights from './OverviewInsights'
import { mockServer } from '../test/server'
import { fakeAc, renderWithProviders } from '../test/render'

function panel(title: RegExp) {
  return screen.getByRole('heading', { name: title }).closest('section') as HTMLElement
}

describe('OverviewInsights', () => {
  it('links the priority panel to the booth table, not the knowledge cards', async () => {
    mockServer()
    renderWithProviders(<OverviewInsights ac={fakeAc(32)} seesCaste />)
    const link = await waitFor(() =>
      within(panel(/highest-priority booths/i)).getByRole('link'))
    expect(link.getAttribute('href')).toBe('/booths')
  })

  it('lists each community once, not one row per booth', async () => {
    mockServer()
    renderWithProviders(<OverviewInsights ac={fakeAc(32)} seesCaste />)
    await waitFor(() => expect(within(panel(/community snapshot/i)).queryAllByRole('listitem').length)
      .toBeGreaterThan(0))
    const names = within(panel(/community snapshot/i)).getAllByRole('listitem')
      .map((li) => li.querySelector('span')?.textContent)
    expect(new Set(names).size).toBe(names.length)
  })

  it('shows the latest news from the `rows` the API returns', async () => {
    mockServer({
      '/acs/32/news': { body: { rows: [{ news_id: 1, title: 'Road opened in Giridih', source: 'Test',
        published: '2026-09-30', similarity: null }], count: 1, issues: [] } },
    })
    renderWithProviders(<OverviewInsights ac={fakeAc(32)} seesCaste />)
    expect(await screen.findByText('Road opened in Giridih')).toBeInTheDocument()
  })

  it('ranks the largest swings', async () => {
    mockServer()
    renderWithProviders(<OverviewInsights ac={fakeAc(32)} seesCaste />)
    await waitFor(() =>
      expect(within(panel(/largest swings/i)).queryAllByRole('listitem').length).toBeGreaterThan(0))
  })

  it('does not ask for community estimates for a user who may not see them', async () => {
    const server = mockServer()
    renderWithProviders(<OverviewInsights ac={fakeAc(32)} seesCaste={false} />)
    await screen.findAllByRole('heading')
    await new Promise((r) => setTimeout(r, 50))
    expect(server.calls.some((c) => c.path.startsWith('/acs/32/caste'))).toBe(false)
    expect(screen.queryByRole('heading', { name: /community snapshot/i })).not.toBeInTheDocument()
  })
})
