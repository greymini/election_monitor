import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import Directory from '../Directory'
import { mockServer } from '../../test/server'
import { fakeAc, renderWithProviders } from '../../test/render'

describe('Directory page', () => {
  it('lists panchayats tab with source line', async () => {
    mockServer({
      '/acs/32/directory/panchayats': {
        body: {
          source: 'Local Government Directory, 01 Oct 2026',
          rows: [{
            area_id: 1, name_en: 'Test GP', name_hi: null, lgd_code: '123',
            block_en: 'Pirtand', has_boundary: true, village_count: 3,
            official_count: 2, mukhiya_2022: 'Winner',
          }],
        },
      },
    })
    renderWithProviders(<Directory ac={fakeAc(32)} />)
    expect(await screen.findByText('Test GP')).toBeInTheDocument()
    expect(screen.getByText(/Local Government Directory/)).toBeInTheDocument()
  })
})
