/** Base map tile source, read from the environment.
 *
 * The only place in this codebase that may name a tile server. There is a test
 * that fails if a tile URL appears anywhere else, and another that fails if the
 * string `cartocdn` appears at all.
 *
 * **Why this module exists.** The map shipped with
 * `https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png` hardcoded.
 * That URL has not changed since the baseline commit `1b0efed`; what changed is
 * CARTO's side of it - their keyless endpoint now returns watermark tiles
 * reading "API key required". Nothing in this repository broke. A hardcoded
 * third-party URL is a dependency on someone else's pricing decision, and it
 * took a code change to react to one. Now it takes an `.env` edit. Recorded in
 * DECISIONS.md D-007.
 *
 * The default is OpenStreetMap's standard tiles, which need no key. They ask
 * for attribution and a sane usage volume, both of which this satisfies:
 * attribution is rendered by Leaflet from `attribution` below, and a
 * constituency map at zoom 11-14 is a few hundred tiles.
 */

/** OpenStreetMap standard tiles. No key, no subdomains needed. */
const DEFAULT_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const DEFAULT_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" ' +
  'rel="noreferrer">OpenStreetMap</a> contributors'
const DEFAULT_MAX_ZOOM = 19

export interface TileConfig {
  url: string
  attribution: string
  maxZoom: number
  /** Only set when the URL contains `{s}`; Leaflet warns if it is passed otherwise. */
  subdomains?: string
  /** True when every value came from the defaults, which the map notes. */
  isDefault: boolean
}

function env(name: string): string | undefined {
  const value = (import.meta.env as Record<string, string | undefined>)[name]
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

export function tileConfig(): TileConfig {
  const url = env('VITE_TILE_URL')
  const attribution = env('VITE_TILE_ATTRIBUTION')
  const maxZoomRaw = env('VITE_TILE_MAX_ZOOM')
  const subdomains = env('VITE_TILE_SUBDOMAINS')

  const parsedZoom = maxZoomRaw === undefined ? undefined : Number(maxZoomRaw)
  const maxZoom =
    parsedZoom !== undefined && Number.isFinite(parsedZoom) && parsedZoom > 0
      ? Math.floor(parsedZoom)
      : DEFAULT_MAX_ZOOM

  const resolvedUrl = url ?? DEFAULT_URL

  return {
    url: resolvedUrl,
    // Attribution follows the URL: a custom tile server with the default
    // OpenStreetMap credit would be crediting the wrong people, which is worse
    // than no credit because it is a false statement about provenance.
    attribution: attribution ?? (url ? '' : DEFAULT_ATTRIBUTION),
    maxZoom,
    // `{s}` is the subdomain placeholder. Passing `subdomains` to a URL without
    // it makes Leaflet log a warning on every tile, and section 4 fails a page
    // with console warnings from app code.
    subdomains: resolvedUrl.includes('{s}') ? (subdomains ?? 'abc') : undefined,
    isDefault: url === undefined,
  }
}

/** How many failed tiles before the map admits the base layer is not coming.
 *
 *  Not 1: a single tile failing is ordinary - a dropped request at the edge of
 *  a pan. A watermarked or 403ing provider fails essentially all of them, and a
 *  screenful at zoom 11 is well over a dozen, so this triggers quickly on a real
 *  outage and not on noise. */
export const TILE_FAILURE_THRESHOLD = 6
