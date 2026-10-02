import { useRef } from 'react'
import { GeoJSON, Pane } from 'react-leaflet'
import type { Feature, FeatureCollection, Geometry } from 'geojson'
import type { Layer, PathOptions } from 'leaflet'

/**
 * Constituency, block and panchayat/ward outlines under the booth markers.
 *
 * **Own pane, below the markers.** Leaflet puts every vector in the overlay
 * pane (z 400) in the order it was added, so polygons added after a filter
 * change would land on top of the booths and swallow their clicks. A dedicated
 * pane at z 350 keeps every boundary underneath whatever order React mounts
 * things in.
 *
 * **Styled by class, not by colour value.** Leaflet writes `stroke` and `fill`
 * as SVG presentation attributes, where `var(--token)` does not resolve, so a
 * colour passed in path options cannot follow the light/dark theme. Each path
 * gets a class instead and `styles/index.css` colours it from the tokens - CSS
 * outranks presentation attributes, so the class wins.
 *
 * **`key` forces a remount.** react-leaflet's GeoJSON reads `data` and `style`
 * once, at mount; changing either afterwards does nothing. The key carries
 * everything the drawing depends on.
 */

export interface BoundaryProps {
  layer: 'ac' | 'block' | 'area'
  name_en?: string
  name_hi?: string | null
  block_id?: number | null
  area_id?: number | null
  seeded?: boolean
  synthetic?: boolean
}

export type BoundaryFeature = Feature<Geometry, BoundaryProps>

export interface Boundaries {
  ac: BoundaryFeature | null
  blocks: FeatureCollection<Geometry, BoundaryProps>
  areas: FeatureCollection<Geometry, BoundaryProps>
  sources: Record<string, { attribution?: string }>
  warnings: Array<{
    ac_number: number
    code: string
    message: string
    params?: Record<string, string | number>
  }>
}

export interface LayerVisibility {
  ac: boolean
  blocks: boolean
  areas: boolean
}

const PANE = 'boundaries'

export default function BoundaryLayers({
  data,
  visible,
  hi,
  blockId,
  areaId,
  unseededLabel,
  syntheticLabel,
  onAreaClick,
}: {
  data: Boundaries
  visible: LayerVisibility
  hi: boolean
  blockId: number | null
  areaId: number | null
  unseededLabel: string
  syntheticLabel: string
  /** A click on a panchayat or ward. Read through a ref: GeoJSON binds its
   *  handlers once, at mount, so a changed callback would otherwise be lost. */
  onAreaClick?: (area: BoundaryProps) => void
}) {
  const areaClick = useRef(onAreaClick)
  areaClick.current = onAreaClick

  const nameOf = (p: BoundaryProps) => (hi && p.name_hi ? p.name_hi : p.name_en) ?? ''

  const tooltip = (feature: BoundaryFeature, layer: Layer) => {
    const p = feature.properties
    const notes = [
      p.seeded === false ? unseededLabel : null,
      p.synthetic ? syntheticLabel : null,
    ].filter(Boolean)
    const text = notes.length ? `${nameOf(p)} (${notes.join('; ')})` : nameOf(p)
    layer.bindTooltip(text, { sticky: true, direction: 'top', className: 'boundary-tooltip' })
  }

  const blockStyle = (feature?: BoundaryFeature): PathOptions => {
    const p = feature?.properties
    const selected = blockId !== null && p?.block_id === blockId
    const dimmed = blockId !== null && !selected
    return {
      pane: PANE,
      className: [
        'boundary-block',
        p?.seeded === false ? 'boundary-unseeded' : '',
        selected ? 'boundary-selected' : '',
        dimmed ? 'boundary-dimmed' : '',
      ].join(' '),
      weight: selected ? 3 : 2,
      fillOpacity: selected ? 0.08 : 0.04,
      dashArray: p?.seeded === false ? '4 4' : undefined,
    }
  }

  const areaStyle = (feature?: BoundaryFeature): PathOptions => {
    const p = feature?.properties
    const selected = areaId !== null && p?.area_id === areaId
    const outside = blockId !== null && p?.block_id !== blockId
    return {
      pane: PANE,
      className: [
        'boundary-area',
        selected ? 'boundary-selected' : '',
        outside ? 'boundary-dimmed' : '',
      ].join(' '),
      weight: selected ? 2.5 : 0.8,
      fillOpacity: selected ? 0.1 : 0,
    }
  }

  const key = `${hi}|${blockId}|${areaId}`

  return (
    <Pane name={PANE} style={{ zIndex: 350 }}>
      {visible.areas && data.areas.features.length > 0 && (
        <GeoJSON
          key={`areas|${key}|${data.areas.features.length}`}
          data={data.areas}
          style={areaStyle as (f?: Feature) => PathOptions}
          onEachFeature={((feature: BoundaryFeature, layer: Layer) => {
            tooltip(feature, layer)
            if (feature.properties.area_id) {
              layer.on('click', () => areaClick.current?.(feature.properties))
            }
          }) as (f: Feature, l: Layer) => void}
        />
      )}
      {visible.blocks && data.blocks.features.length > 0 && (
        <GeoJSON
          key={`blocks|${key}|${data.blocks.features.length}`}
          data={data.blocks}
          style={blockStyle as (f?: Feature) => PathOptions}
          // With areas showing, blocks are the frame and the areas carry the
          // hover; tooltips on both would stack two labels under the cursor.
          interactive={!visible.areas || data.areas.features.length === 0}
          onEachFeature={tooltip as (f: Feature, l: Layer) => void}
        />
      )}
      {visible.ac && data.ac && (
        <GeoJSON
          key={`ac|${data.ac.properties.layer}|${JSON.stringify(data.ac.geometry).length}`}
          data={data.ac}
          style={{ pane: PANE, className: 'boundary-ac', weight: 3, fill: false, dashArray: '8 5' }}
          interactive={false}
        />
      )}
    </Pane>
  )
}
