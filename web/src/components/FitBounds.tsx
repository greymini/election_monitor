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
 *     `Bounds are not valid`. Nothing to fit, so the view is left as it is -
 *     the caller is already showing a "no booths" note.
 *   * **One marker.** A bounds of one point has zero extent; `fitBounds` would
 *     zoom to maximum, filling the screen with one dot in a sea of tiles. A
 *     single marker gets `setView` at a readable zoom instead.
 */
export default function FitBounds({
  points,
  singleZoom = 14,
  padding = 0.12,
}: {
  points: LatLngTuple[]
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

  useEffect(() => {
    if (points.length === 0) return
    if (points.length === 1) {
      map.setView(points[0], Math.max(map.getZoom(), singleZoom))
      return
    }
    map.fitBounds(points, {
      paddingTopLeft: [map.getSize().x * padding, map.getSize().y * padding],
      paddingBottomRight: [map.getSize().x * padding, map.getSize().y * padding],
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map, singleZoom, padding])

  return null
}
