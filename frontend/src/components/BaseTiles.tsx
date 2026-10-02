import { useEffect, useRef } from 'react'
import { TileLayer } from 'react-leaflet'

import { TILE_FAILURE_THRESHOLD, tileConfig } from '../lib/tiles'

/**
 * The base map layer, and the detection that it has failed.
 *
 * The tile source comes from `lib/tiles.ts` and nowhere else. This component
 * does not name a URL, and a test fails the build if one appears here.
 *
 * **Tile failure must not break the map.** When the provider stops serving -
 * which is exactly what happened when CARTO started returning "API key
 * required" watermarks - the booth markers, filters, legend and booth cards are
 * all still correct and still the point of the page. Leaflet raises `tileerror`
 * per failed tile; past a threshold this reports upward once so the page can say
 * so, and the map keeps working on a plain background.
 *
 * `onFailure` is called at most once per mount. Calling it per tile would set
 * state dozens of times during a single failed pan.
 */
export default function BaseTiles({ onFailure }: { onFailure?: () => void }) {
  const config = tileConfig()
  const failures = useRef(0)
  const reported = useRef(false)

  // A new tile source is a fresh verdict: a URL change should not inherit the
  // previous provider's failures.
  useEffect(() => {
    failures.current = 0
    reported.current = false
  }, [config.url])

  return (
    <TileLayer
      url={config.url}
      attribution={config.attribution}
      maxZoom={config.maxZoom}
      {...(config.subdomains ? { subdomains: config.subdomains } : {})}
      eventHandlers={{
        tileerror: () => {
          failures.current += 1
          if (!reported.current && failures.current >= TILE_FAILURE_THRESHOLD) {
            reported.current = true
            onFailure?.()
          }
        },
        // A tile that loads after a bad patch means the provider is alive and
        // the earlier errors were noise, so the count should not creep toward
        // the threshold across an entire session of ordinary panning.
        tileload: () => {
          if (!reported.current) failures.current = 0
        },
      }}
    />
  )
}
