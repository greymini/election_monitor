/** API client. One place that attaches the token, handles 401 and formats errors. */

const BASE = import.meta.env.VITE_API_BASE ?? '/api'
const TOKEN_KEY = 'giridih.token'

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
  const token = getToken()
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  })

  if (response.status === 401) {
    setSession(null)
    window.dispatchEvent(new CustomEvent('giridih:unauthorised'))
    throw new ApiError(401, 'Session expired. Please sign in again.')
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
}

/** CSV export hits the same endpoint with format=csv and streams to a download. */
export async function downloadCsv(path: string, filename: string) {
  const token = getToken()
  const joiner = path.includes('?') ? '&' : '?'
  const response = await fetch(`${BASE}${path}${joiner}format=csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) throw new ApiError(response.status, 'Export failed')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export async function login(phone: string, password: string): Promise<Session> {
  const session = await api.post<Session>('/auth/login', { phone, password })
  setSession(session)
  return session
}

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
