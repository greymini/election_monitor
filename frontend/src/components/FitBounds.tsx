import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import type { LatLngTuple } from 'leaflet'

/**
 * Fit the view to the markers, on load and whenever the set changes.
 *
 * Without this the map opens on a hardcoded per-AC centre at a fixed zoom, so
 * filtering to one block leaves the markers in a corner or off screen entirely,
 * and an AC whose centre constant is approximate opens looking empty.
 *
 * The two degenerate cases are the reason this is a component rather than three
 * lines inline:
 *
 *   * **No markers.** `fitBounds` on an empty bounds object throws
 *     `Bounds are not valid`. With a `fallback` the view goes there (the
 *     constituency's centre); without one it is left as it is. Leaving it was
 *     the old behaviour, and it meant switching to a constituency with no
 *     booths kept showing the previous one's map under the new heading.
 *   * **One marker.** A bounds of one point has zero extent; `fitBounds` would
 *     zoom to maximum, filling the screen with one dot in a sea of tiles. A
 *     single marker gets `setView` at a readable zoom instead.
 */
export default function FitBounds({
  points,
  fallback,
  fallbackBounds,
  fallbackZoom = 11,
  singleZoom = 14,
  padding = 0.12,
}: {
  points: LatLngTuple[]
  /** Where to look when there are no points at all. */
  fallback?: LatLngTuple
  /** Better than `fallback` when known: the constituency's own outline. */
  fallbackBounds?: [LatLngTuple, LatLngTuple]
  fallbackZoom?: number
  /** Zoom used when there is exactly one point. */
  singleZoom?: number
  /** Fraction of the viewport left as margin around the markers. */
  padding?: number
}) {
  const map = useMap()

  // The dependency is the coordinates themselves, not the array identity: the
  // parent rebuilds this array on every render, and depending on identity would
  // refit - and so fight the user's pan and zoom - on every one of them.
  const key = points.map(([lat, lng]) => `${lat.toFixed(5)},${lng.toFixed(5)}`).join('|')
  const fallbackKey = `${fallback?.join(',') ?? ''}|${fallbackBounds?.flat().join(',') ?? ''}`

  useEffect(() => {
    if (points.length === 0) {
      if (fallbackBounds) map.fitBounds(fallbackBounds)
      else if (fallback) map.setView(fallback, fallbackZoom)
      return
    }
    if (points.length === 1) {
      map.setView(points[0], Math.max(map.getZoom(), singleZoom))
      return
    }
    map.fitBounds(points, {
      paddingTopLeft: [map.getSize().x * padding, map.getSize().y * padding],
      paddingBottomRight: [map.getSize().x * padding, map.getSize().y * padding],
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, fallbackKey, map, singleZoom, padding])

  return null
}
