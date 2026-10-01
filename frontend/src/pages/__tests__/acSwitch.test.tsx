import type { ComponentType } from 'react'
import { describe, expect, it } from 'vitest'
import { waitFor } from '@testing-library/react'

import Caste from '../Caste'
import LocalPolls from '../LocalPolls'
import News from '../News'
import Results from '../Results'
import Voters from '../Voters'
import type { AcState } from '../../lib/ac'
import { mockServer } from '../../test/server'
import { fakeAc, renderWithProviders } from '../../test/render'

/**
 * Switching constituency refetches every page's data.
 *
 * These five pages keyed their queries without the AC number (e.g.
 * ['results', 'VS-2024']), so after a switch React Query served the previous
 * constituency's cached rows under the new one's name for up to five minutes.
 */
const PAGES: Array<[string, ComponentType<{ ac: AcState }>, RegExp]> = [
  ['Results', Results, /^\/acs\/(\d+)\/results\/[^/]+\/booths/],
  ['News', News, /^\/acs\/(\d+)\/news(\?|$)/],
  ['Voters', Voters, /^\/acs\/(\d+)\/rolls\/changes/],
  ['LocalPolls', LocalPolls, /^\/acs\/(\d+)\/local-results/],
  ['Caste', Caste, /^\/acs\/(\d+)\/caste(\?|$)/],
]

describe.each(PAGES)('%s', (_name, Page, endpoint) => {
  it('fetches again for the new constituency', async () => {
    const server = mockServer()
    const { rerender, client } = renderWithProviders(<Page ac={fakeAc(32)} />)
    await waitFor(() =>
      expect(server.calls.some((c) => endpoint.exec(c.path)?.[1] === '32')).toBe(true))

    rerender(<Page ac={fakeAc(33)} />)
    void client
    await waitFor(() =>
      expect(server.calls.some((c) => endpoint.exec(c.path)?.[1] === '33')).toBe(true))
  })

  it('waits for a constituency instead of erroring', async () => {
    const server = mockServer()
    const { container } = renderWithProviders(<Page ac={fakeAc(null)} />)
    await new Promise((r) => setTimeout(r, 50))
    expect(server.calls.filter((c) => c.path.startsWith('/acs/'))).toEqual([])
    expect(container.textContent).not.toMatch(/no constituency selected/i)
  })
})
