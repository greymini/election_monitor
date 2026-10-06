/** API client. One place that attaches the token, handles 401 and formats errors. */

/**
 * Fixtures are **not** imported statically.
 *
 * N9: a static import put the whole fixture module - 305 booths, their prior
 * election, the area list - into the main chunk, which grew from 123 kB to
 * 319 kB. Every production user downloaded invented election data they could
 * never see, because `USE_FIXTURES` is false in that build. Tree-shaking cannot
 * remove it: `import.meta.env.VITE_FIXTURES` is replaced at build time, so the
 * branch does fold away, but the module was already in the graph by then.
 *
 * A dynamic `import()` inside the branch puts it in its own chunk that is only
 * ever requested when the branch is taken. Cached after the first call, because
 * `request` runs per API call and re-importing per call would serialise them
 * behind a module fetch.
 */
type FixtureLookup = (path: string, method?: string, body?: unknown) => unknown

let fixtureLookup: FixtureLookup | null = null

async function loadFixtures(): Promise<FixtureLookup> {
  if (fixtureLookup === null) {
    const module = await import('../fixtures/responses')
    fixtureLookup = module.fixtureFor
  }
  return fixtureLookup
}

const BASE = import.meta.env.VITE_API_BASE ?? '/api'
const TOKEN_KEY = 'giridih.token'

/**
 * Fixture mode. With VITE_FIXTURES=1 every request is answered from
 * src/fixtures instead of the network.
 *
 * It lives here, in the one place that already owns every request, rather than
 * in the pages - so a page reviewed against fixtures is the same page that will
 * run against Postgres, not a mock of it. No page contains any fixture-handling
 * code at all.
 *
 * Every fixture response carries a `fixture` field, which the pages surface as
 * a banner, so a reviewable page cannot be mistaken for a loaded one.
 */
const USE_FIXTURES = import.meta.env.VITE_FIXTURES === '1'

export interface Session {
  access_token: string
  role: 'admin' | 'strategist' | 'block'
  name: string
  block_id: number | null
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setSession(session: Session | null) {
  try {
    if (session) localStorage.setItem(TOKEN_KEY, session.access_token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private browsing - the session simply will not persist */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (USE_FIXTURES) {
    const fixtureFor = await loadFixtures()
    let parsed: unknown
    try {
      parsed = init.body ? JSON.parse(String(init.body)) : undefined
    } catch {
      parsed = undefined
    }
    const canned = fixtureFor(path, init.method ?? 'GET', parsed)
    if (canned === undefined) {
      throw new ApiError(404, `No fixture for ${path}. Add one to src/fixtures/responses.ts.`)
    }
    // A tick of delay so loading states are actually reachable in review.
    await new Promise((resolve) => setTimeout(resolve, 60))
    return canned as T
  }

  const token = getToken()
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  })

  // A 401 on any call but the login itself means the session is over. On the
  // login call it means the password was wrong, and must say so: treating it
  // as an expiry told a user who mistyped that their session had expired.
  if (response.status === 401 && path !== '/auth/login') {
    setSession(null)
    window.dispatchEvent(new CustomEvent('giridih:unauthorised'))
    throw new ApiError(401, 'Session expired. Please sign in again.')
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail = formatDetail(body.detail) ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** FastAPI sends `detail` as a string for HTTPException and as a list of
 *  `{loc, msg}` objects for a validation error (422). Rendering the list
 *  directly showed "[object Object]". */
export function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : null))
      .filter((m): m is string => Boolean(m))
    return messages.length ? messages.join('; ') : null
  }
  return null
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
}

/** Rows as CSV text: a header from the first row's keys, RFC 4180 quoting. */
export function toCsv(rows: Array<Record<string, unknown>>): string {
  if (rows.length === 0) return ''
  const keys = Object.keys(rows[0])
  const cell = (v: unknown) => {
    if (v === null || v === undefined) return ''
    const text = Array.isArray(v) ? v.join('; ') : String(v)
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [keys.join(','), ...rows.map((r) => keys.map((k) => cell(r[k])).join(','))].join('\r\n')
}

/**
 * CSV export: the same endpoint with format=csv, saved as a download.
 *
 * Failures are shown, not swallowed. Every caller fired this with `void`, so an
 * export that failed - a 403, a 500, an expired session - did nothing at all.
 * A 401 ends the session like any other request. In fixture mode the CSV is
 * built from the fixture rows instead of calling an API that is not there.
 */
export async function downloadCsv(path: string, filename: string): Promise<void> {
  try {
    let blob: Blob
    if (USE_FIXTURES) {
      const fixtureFor = await loadFixtures()
      const data = fixtureFor(path) as { rows?: Array<Record<string, unknown>> } | undefined
      blob = new Blob([toCsv(data?.rows ?? [])], { type: 'text/csv' })
    } else {
      const token = getToken()
      const joiner = path.includes('?') ? '&' : '?'
      const response = await fetch(`${BASE}${path}${joiner}format=csv`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (response.status === 401) {
        setSession(null)
        window.dispatchEvent(new CustomEvent('giridih:unauthorised'))
        throw new ApiError(401, 'Session expired. Please sign in again.')
      }
      if (!response.ok) throw new ApiError(response.status, `Export failed (${response.status})`)
      blob = await response.blob()
    }
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    // Revoked on the next tick: revoking synchronously after click() can cancel
    // the download before the browser has started reading the blob.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    window.alert(error instanceof Error ? error.message : 'Export failed')
  }
}

export async function login(username: string, password: string): Promise<Session> {
  if (USE_FIXTURES) {
    const session: Session = {
      access_token: 'fixture-token', role: 'admin',
      name: 'Fixture Analyst', block_id: null,
    }
    setSession(session)
    return session
  }
  const session = await api.post<Session>('/auth/login', { username, password })
  setSession(session)
  return session
}

/** Whether this build is serving fixtures. Pages use it only to label
 *  themselves; they never branch on it for behaviour. */
export const isFixtureMode = () => USE_FIXTURES

export interface Me {
  user_id: number
  name: string
  role: 'admin' | 'strategist' | 'block'
  block_id: number | null
  sees_caste: boolean
  daily_token_budget: number
}

export const getMe = () => api.get<Me>('/auth/me')

/** Server-declared feature flags and build metadata, read once at boot.
 *
 * Unauthenticated on purpose: the login screen needs to know whether the chat
 * panel exists before anyone has a token. Without this the only way to turn the
 * assistant off was to rebuild the frontend, which is why the audit found the
 * feature could not be parked (A5).
 */
export interface AppConfig {
  chat_enabled: boolean
  /** Identity only, no figures. Present so the header switcher can render
   *  before the user has a token. */
  acs: Array<{
    ac_number: number
    name_en: string
    name_hi: string
    reservation: string
    verified: boolean
  }>
  default_ac: number | null
  version: string
  build_time: string
}

export const getConfig = () => api.get<AppConfig>('/config')
