import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

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
})
