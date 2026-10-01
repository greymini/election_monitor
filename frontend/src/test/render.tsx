import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

export function testQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } },
  })
}

/** Render inside the providers main.tsx sets up, at `route`. */
export function renderWithProviders(
  ui: ReactElement,
  { route = '/', client = testQueryClient() }: { route?: string; client?: QueryClient } = {},
) {
  // As a `wrapper`, so rerender() keeps the providers (and the same cache).
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
  const result = render(ui, { wrapper: Wrapper })
  return { ...result, client }
}

import type { AcState } from '../lib/ac'

/** A fixed constituency for components that take `ac` as a prop. */
export function fakeAc(acNumber: number | null = 32): AcState {
  return {
    acNumber,
    ac: null,
    all: [],
    setAc: () => {},
    path: (p: string) => {
      if (acNumber === null) throw new Error('no constituency selected yet')
      return `/acs/${acNumber}${p}`
    },
  }
}
