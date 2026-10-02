import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { AreaNewsList } from './AreaNews'
import { mockServer } from '../test/server'
import { fakeAc, renderWithProviders } from '../test/render'

const response = (extra: object) => ({
  area: { area_id: 7, name_en: 'Harladih', name_hi: 'Harladih', kind: 'panchayat',
          block_id: 3202, block_en: 'Pirtand Block', block_hi: 'पीरटांड़ प्रखंड' },
  window: { basis: 'poll_date', poll_date: '2024-11-20', election_label: 'VS-2024',
            date_from: '2024-10-06', date_to: '2024-11-23' },
  level: 'block', counts: { area: 0, block: 1, ac: 5, state: 9 },
  rows: [{ news_id: 1, published: '2024-11-10', source: 'Prabhat Khabar',
           title: 'Pirtand campaign heats up', url: 'https://x', parties: ['JMM'],
           issues: ['roads'], relevance: 0.6 }],
  result: null, result_note: 'not loaded',
  ...extra,
})

describe('AreaNewsList', () => {
  it('says when it fell back from the panchayat to its block', async () => {
    mockServer({ '/acs/32/areas/7/news': { body: response({}) } })
    renderWithProviders(<AreaNewsList ac={fakeAc(32)} areaId={7} election="VS-2024" />)
    expect(await screen.findByText('Pirtand campaign heats up')).toBeInTheDocument()
    expect(screen.getByText(/No news names Harladih in this period; showing news for Pirtand Block/))
      .toBeInTheDocument()
    expect(screen.getByText(/VS-2024: polled/)).toBeInTheDocument()
  })

  it('shows the result as context, and says when it is missing', async () => {
    mockServer({ '/acs/32/areas/7/news': { body: response({}) } })
    renderWithProviders(<AreaNewsList ac={fakeAc(32)} areaId={7} election="VS-2024" />)
    expect(await screen.findByText(/polling-station list/)).toBeInTheDocument()
    expect(screen.getByText(/does not show that a story changed a vote/)).toBeInTheDocument()
  })

  it('says there is no news at any level rather than showing an empty list', async () => {
    mockServer({ '/acs/32/areas/7/news': { body: response({
      level: null, rows: [], counts: { area: 0, block: 0, ac: 0, state: 0 } }) } })
    renderWithProviders(<AreaNewsList ac={fakeAc(32)} areaId={7} election="VS-2019" />)
    expect(await screen.findByText(/at any level/)).toBeInTheDocument()
  })

  it('asks for the election window', async () => {
    const server = mockServer({ '/acs/32/areas/7/news': { body: response({}) } })
    renderWithProviders(<AreaNewsList ac={fakeAc(32)} areaId={7} election="VS-2024" />)
    await screen.findByText('Pirtand campaign heats up')
    expect(server.calls.some((c) => c.path === '/acs/32/areas/7/news?election_label=VS-2024'))
      .toBe(true)
  })
})
