/**
 * A fetch stand-in for component and page tests.
 *
 * By default every GET is answered from src/fixtures/responses.ts - the same
 * data fixture mode serves - so a page test sees realistic payloads while the
 * real api.ts request path (token, 401, error formatting) runs. Individual
 * routes can be overridden with a status, a body, or a function.
 */
import { vi } from 'vitest'

import { fixtureFor } from '../fixtures/responses'

export type Handler =
  | { status?: number; body?: unknown }
  | ((url: URL, init: RequestInit | undefined) => { status?: number; body?: unknown })

export interface MockServer {
  calls: Array<{ method: string; path: string; body?: unknown }>
  /** Override a route. `key` is "METHOD /path" or "/path" (any method),
   *  matched on the path without the /api prefix and without the query. */
  on(key: string, handler: Handler): void
}

export function mockServer(overrides: Record<string, Handler> = {}): MockServer {
  const handlers = new Map(Object.entries(overrides))
  const calls: MockServer['calls'] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname.replace(/^\/api/, '')
    const method = (init?.method ?? 'GET').toUpperCase()
    let parsed: unknown
    try {
      parsed = init?.body ? JSON.parse(String(init.body)) : undefined
    } catch {
      parsed = init?.body
    }
    calls.push({ method, path: path + url.search, body: parsed })

    const handler = handlers.get(`${method} ${path}`) ?? handlers.get(path)
    let status = 200
    let body: unknown
    if (handler) {
      const out = typeof handler === 'function' ? handler(url, init) : handler
      status = out.status ?? 200
      body = out.body
    } else if (method === 'GET') {
      body = fixtureFor(path + url.search)
      if (body === undefined) {
        status = 404
        body = { detail: `no fixture for ${path}` }
      }
    } else {
      status = 404
      body = { detail: `no handler for ${method} ${path}` }
    }
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  return { calls, on: (key, handler) => { handlers.set(key, handler) } }
}

/** Signed in as `role`: a token in storage and /auth/me answering for it. */
export function signIn(server: MockServer, role: 'admin' | 'strategist' | 'block' = 'admin') {
  localStorage.setItem('giridih.token', `test-${role}`)
  server.on('/auth/me', {
    body: {
      user_id: 1, name: `Test ${role}`, role, block_id: role === 'block' ? 3202 : null,
      sees_caste: role !== 'block', daily_token_budget: 150000,
    },
  })
}
