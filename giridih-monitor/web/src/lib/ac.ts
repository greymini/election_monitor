/**
 * The selected constituency, and how every request gets scoped to it.
 *
 * The AC lives in the URL (`?ac=32`) so a view is shareable - the spec asks for
 * that explicitly, and "look at this booth" is the most common thing anyone
 * sends a colleague. It is mirrored to localStorage so a fresh tab opens where
 * you left off, and falls back to whatever `/config` reports first.
 *
 * `acPath` is the only way pages should build a data URL. Every data route is
 * under `/acs/{ac_number}/`, and a page that forgets the prefix would 404 at
 * best and, if the old redirect is still in place, silently show Giridih's
 * numbers under another constituency's heading at worst.
 */

import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { AppConfig } from './api'

const STORAGE_KEY = 'giridih.ac'

export interface AcSummary {
  ac_number: number
  name_en: string
  name_hi: string
  reservation: string
  verified: boolean
}

function remembered(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? Number(raw) : NaN
    return Number.isFinite(parsed) ? parsed : null
  } catch {
    return null
  }
}

function remember(acNumber: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(acNumber))
  } catch {
    /* private browsing - the choice just will not persist */
  }
}

/** Prefix a data path with the selected constituency. */
export function acPath(acNumber: number, path: string): string {
  const clean = path.startsWith('/') ? path : `/${path}`
  return `/acs/${acNumber}${clean}`
}

export interface AcState {
  /** The selected AC, or null while /config is still loading. */
  acNumber: number | null
  ac: AcSummary | null
  all: AcSummary[]
  setAc: (acNumber: number) => void
  /** Scope a path to the selected AC. Throws if called before one is known. */
  path: (p: string) => string
}

/**
 * Resolution order: `?ac=` in the URL, then localStorage, then the first AC
 * `/config` lists. The URL wins so a shared link always shows what the sender
 * saw, whatever the recipient last looked at.
 */
export function useAc(config: AppConfig | undefined): AcState {
  const [params, setParams] = useSearchParams()
  const all = useMemo<AcSummary[]>(() => config?.acs ?? [], [config])

  const fromUrl = Number(params.get('ac'))
  const valid = (n: number) => all.some((a) => a.ac_number === n)

  let acNumber: number | null = null
  if (Number.isFinite(fromUrl) && valid(fromUrl)) {
    acNumber = fromUrl
  } else {
    const stored = remembered()
    if (stored !== null && valid(stored)) acNumber = stored
    else if (all.length > 0) acNumber = all[0].ac_number
  }

  // Put the resolved AC in the URL so the address bar always reflects what is
  // on screen, without adding a history entry for a choice the user did not
  // make.
  useEffect(() => {
    if (acNumber === null) return
    remember(acNumber)
    if (Number(params.get('ac')) !== acNumber) {
      const next = new URLSearchParams(params)
      next.set('ac', String(acNumber))
      setParams(next, { replace: true })
    }
  }, [acNumber, params, setParams])

  const setAc = useCallback(
    (next: number) => {
      remember(next)
      const updated = new URLSearchParams(params)
      updated.set('ac', String(next))
      setParams(updated)
    },
    [params, setParams],
  )

  const path = useCallback(
    (p: string) => {
      if (acNumber === null) {
        throw new Error('no constituency selected yet')
      }
      return acPath(acNumber, p)
    },
    [acNumber],
  )

  return {
    acNumber,
    ac: all.find((a) => a.ac_number === acNumber) ?? null,
    all,
    setAc,
    path,
  }
}
