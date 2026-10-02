import { describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import News from '../News'
import { mockServer } from '../../test/server'
import { fakeAc, renderWithProviders } from '../../test/render'

const item = (extra: object) => ({
  news_id: 1, published: '2026-09-30', source: 'Prabhat Khabar', title: 'Water crisis in Pirtand',
  summary_hi: null, summary_en: 'Hand pumps dry', issues: ['water'], parties: [], url: 'https://x',
  ...extra,
})

describe('News', () => {
  it('never renders "NaN% match" for an item without a similarity score', async () => {
    mockServer({ '/acs/32/news': { body: { rows: [item({})], count: 1, issues: [] } } })
    renderWithProviders(<News ac={fakeAc(32)} />)
    expect(await screen.findByText('Water crisis in Pirtand')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/NaN/)
  })

  it('shows the match percentage when the search ranked by similarity', async () => {
    mockServer({ '/acs/32/news': { body: { rows: [item({ similarity: 0.82 })], count: 1, issues: [] } } })
    renderWithProviders(<News ac={fakeAc(32)} />)
    expect(await screen.findByText(/82% match/)).toBeInTheDocument()
  })

  it('shows party position as mentions, not support', async () => {
    mockServer()
    renderWithProviders(<News ac={fakeAc(32)} />)
    expect(await screen.findByText('Party position in the news')).toBeInTheDocument()
    expect(screen.getByText(/not how many people support it/)).toBeInTheDocument()
    expect(screen.getAllByText('1 · 50%')).toHaveLength(2)
  })

  it('says when items were labelled by keyword rules and tone is not analysed', async () => {
    mockServer()
    renderWithProviders(<News ac={fakeAc(32)} />)
    expect(await screen.findByText(/Tone is not analysed yet/)).toBeInTheDocument()
    expect((await screen.findAllByText('keyword-labelled')).length).toBeGreaterThan(0)
  })

  it('the Jharkhand toggle asks for state scope and shows news that names no seat', async () => {
    const server = mockServer()
    renderWithProviders(<News ac={fakeAc(32)} />)
    await screen.findByText('गिरिडीह में सड़क निर्माण')
    expect(screen.queryByText(/एसआईआर के विरोध में/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'All Jharkhand' }))
    expect(await screen.findByText(/एसआईआर के विरोध में/)).toBeInTheDocument()
    expect(server.calls.some((c) => c.path.startsWith('/acs/32/news?') && c.path.includes('scope=state')))
      .toBe(true)
    expect(server.calls.some((c) => c.path.startsWith('/acs/32/news/summary') && c.path.includes('scope=state')))
      .toBe(true)
  })

  it('filters by party', async () => {
    const server = mockServer()
    renderWithProviders(<News ac={fakeAc(32)} />)
    await screen.findByText('Party position in the news')
    fireEvent.change(screen.getByLabelText('Party'), { target: { value: 'JLKM' } })
    await waitFor(() => expect(screen.queryByText('गिरिडीह में सड़क निर्माण')).not.toBeInTheDocument())
    expect(screen.getAllByText(/JLKM की पदयात्रा/).length).toBeGreaterThan(0)
    expect(server.calls.some((c) => c.path.includes('party=JLKM'))).toBe(true)
  })
})
