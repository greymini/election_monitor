import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'

import Booths from '../Booths'
import Results from '../Results'
import { mockServer } from '../../test/server'
import { fakeAc, renderWithProviders } from '../../test/render'

describe('Results', () => {
  it('lets the dropdown change the election even on /results/:label', async () => {
    const server = mockServer()
    renderWithProviders(
      <Routes><Route path="/results/:electionLabel" element={<Results ac={fakeAc(32)} />} /></Routes>,
      { route: '/results/VS-2024' },
    )
    const select = await screen.findByRole('combobox', { name: /election/i })
    await screen.findByRole('option', { name: 'VS-2019' })
    await userEvent.selectOptions(select, 'VS-2019')
    await waitFor(() =>
      expect(server.calls.some((c) => c.path.startsWith('/acs/32/results/VS-2019/booths'))).toBe(true))
  })
})

describe('Booths', () => {
  it('offers only the elections this constituency has results for', async () => {
    mockServer()
    renderWithProviders(<Booths ac={fakeAc(32)} />)
    const select = await screen.findByRole('combobox', { name: /election/i })
    await waitFor(() => expect(select.querySelectorAll('option').length).toBe(3))
    const labels = [...select.querySelectorAll('option')].map((o) => o.textContent)
    expect(labels).toEqual(['VS-2024', 'VS-2019', 'LS-2024'])   // VS-2014 has no results
  })
})
