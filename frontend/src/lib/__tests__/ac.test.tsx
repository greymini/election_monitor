import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'

import { useAc } from '../ac'
import type { AppConfig } from '../api'

const ACS = [31, 32, 33].map((n) => ({
  ac_number: n, name_en: `AC ${n}`, name_hi: `AC ${n}`, reservation: 'GEN', verified: n === 32,
}))
const config = (default_ac: number | null): AppConfig =>
  ({ chat_enabled: false, acs: ACS, default_ac, version: 't', build_time: 'test' } as AppConfig)

function wrapper(route: string) {
  return ({ children }: { children: ReactNode }) =>
    <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
}

describe('useAc', () => {
  it('starts on the server default, not simply the first AC listed', () => {
    const { result } = renderHook(() => useAc(config(32)), { wrapper: wrapper('/') })
    expect(result.current.acNumber).toBe(32)
  })

  it('prefers the URL, then the remembered choice, then the default', () => {
    localStorage.setItem('giridih.ac', '33')
    expect(renderHook(() => useAc(config(32)), { wrapper: wrapper('/?ac=31') })
      .result.current.acNumber).toBe(31)
    localStorage.setItem('giridih.ac', '33')   // the render above remembered 31
    expect(renderHook(() => useAc(config(32)), { wrapper: wrapper('/') })
      .result.current.acNumber).toBe(33)
  })

  it('ignores an AC the server does not list', () => {
    const { result } = renderHook(() => useAc(config(32)), { wrapper: wrapper('/?ac=99') })
    expect(result.current.acNumber).toBe(32)
  })

  it('scopes paths and refuses before an AC is known', () => {
    const { result } = renderHook(() => useAc(undefined), { wrapper: wrapper('/') })
    expect(result.current.acNumber).toBeNull()
    expect(() => result.current.path('/summary')).toThrow()
    const ready = renderHook(() => useAc(config(32)), { wrapper: wrapper('/') }).result.current
    expect(ready.path('/summary')).toBe('/acs/32/summary')
  })
})
