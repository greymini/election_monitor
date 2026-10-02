import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { CircleMarker, MapContainer, Tooltip as LeafletTooltip } from 'react-leaflet'
import type { LatLngTuple } from 'leaflet'

import BaseTiles from '../components/BaseTiles'
import { AreaNewsDrawer } from '../components/AreaNews'
import BoothDrawer from '../components/BoothDrawer'
import BoundaryLayers from '../components/BoundaryLayers'
import type { Boundaries, BoundaryProps, LayerVisibility } from '../components/BoundaryLayers'
import DivergingLegend from '../components/DivergingLegend'
import FitBounds from '../components/FitBounds'
import SequentialLegend from '../components/SequentialLegend'
import { FixtureBanner } from '../components/Provenance'
import { ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'
import { divergingPartyColor, sequentialColor } from '../lib/tokens'

import 'leaflet/dist/leaflet.css'

/**
 * Booth map (spec §7.3), with the three audit fixes.
 *
 * **F1, the one-sided ramp.** The audited version fed `margin_pct` - which is
 * unsigned by construction, winner minus runner-up - straight into a diverging
 * colour scale. `divergingColor` maps −max to index 0 and +max to index 10, so
 * an always-positive input only ever reached indices 5–10: every booth rendered
 * somewhere between neutral grey and strong red regardless of who won, while
 * the legend underneath told the reader the two arms meant opposite things. The
 * comment above the call even stated the requirement the code did not implement.
 * The map now colours by `signed_margin_pct`, which the view signs by the AC's
 * contest pair, and a booth won by neither pair party is grey with a legend
 * entry saying so.
 *
 * **F2, the missing filters.** The spec asks for metric, year, block and area.
 * Three of the four did not exist, and the year parameter that did exist was
 * accepted and ignored - a caller asking for VS-2019 got 2024 numbers back with
 * "VS-2019" in the response metadata. All four are wired now.
 *
 * **F3, the meaningless marker size.** Radius came from
 * `sqrt((electors ?? 400) / 40)`, and electors was NULL for every booth, so
 * every marker computed sqrt(10) and clamped to the minimum while the caption
 * asserted "marker size is the electorate". Size now comes from real electors,
 * and a booth whose electorate is unknown is drawn as a hollow ring rather than
 * silently sized as if it were small.
 *
 * And the four from the hardening brief:
 *
 * **Base tiles (10).** The CARTO URL is gone; `lib/tiles.ts` reads the source
 * from the environment and defaults to OpenStreetMap. Tile failure now degrades
 * to a notice over a plain background instead of a blank page - the booth data
 * is the point of this screen and none of it depends on the basemap.
 *
 * **Party-coloured ramp (11).** `divergingPartyColor` ends on the contest
 * pair's own colours, the same ones the legend chips use, so marker and legend
 * agree by construction and every AC gets its own pair rather than JMM/BJP's.
 *
 * **fitBounds (12).** The view followed a hardcoded per-AC centre at a fixed
 * zoom, so filtering to one block left its booths off screen. It now fits the
 * markers on load and on every filter change, and handles zero and one marker
 * without throwing.
 *
 * **Filter labels (14).** "All" and "Block" were concatenated into "All Block".
 * Each label is one key now, in both languages.
 */

interface Props {
  ac: AcState
}

interface Feature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] } | null
  properties: Record<string, unknown>
}

interface Collection {
  type: 'FeatureCollection'
  features: Feature[]
  meta: {
    count: number
    ungeocoded: number
    electors_known: number
    metric: string
    ac_number: number
    contest?: { party_a: string; party_b: string } | null
  }
  fixture?: string | null
}

/** Metrics the map can colour by, and how. */
const METRICS = {
  signed_margin_pct: { diverging: true, max: 30, label: 'map.signedMargin' },
  margin_pct: { diverging: false, max: 40, label: 'map.margin' },
  turnout_pct: { diverging: false, max: 100, label: 'map.turnout' },
  new_voter_pct: { diverging: false, max: 25, label: 'map.newVoters' },
  floating_pct: { diverging: false, max: 60, label: 'map.floating' },
  margin_stddev: { diverging: false, max: 20, label: 'map.volatility' },
} as const

type MetricKey = keyof typeof METRICS

const CENTRES: Record<number, [number, number]> = {
  32: [24.1854, 86.3094],
  31: [24.2500, 86.1000],
  33: [23.8700, 85.9500],
  42: [23.9500, 86.3500],
  61: [23.3800, 85.8800],
  65: [23.4400, 85.3200],
}

/** Which boundary layers are drawn. A per-viewer convenience, so it lives in
 *  localStorage; every access is guarded because private windows throw. */
const LAYERS_KEY = 'giridih.map.boundaries'
const DEFAULT_LAYERS: LayerVisibility & { on: boolean } = {
  on: true, ac: true, blocks: true, areas: true,
}

function rememberedLayers(): typeof DEFAULT_LAYERS {
  try {
    const raw = localStorage.getItem(LAYERS_KEY)
    return raw ? { ...DEFAULT_LAYERS, ...JSON.parse(raw) } : DEFAULT_LAYERS
  } catch {
    return DEFAULT_LAYERS
  }
}

export default function MapExplorer({ ac }: Props) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [metric, setMetric] = useState<MetricKey>('signed_margin_pct')
  const [election, setElection] = useState('VS-2024')
  const [blockId, setBlockId] = useState<string>('')
  const [areaId, setAreaId] = useState<string>('')
  const [selected, setSelected] = useState<string | null>(null)
  const [areaPanel, setAreaPanel] = useState<BoundaryProps | null>(null)
  const [tilesFailed, setTilesFailed] = useState(false)
  const [tileNoticeDismissed, setTileNoticeDismissed] = useState(false)
  const [layers, setLayers] = useState(rememberedLayers)

  const updateLayers = (patch: Partial<typeof DEFAULT_LAYERS>) => {
    setLayers((current) => {
      const next = { ...current, ...patch }
      try {
        localStorage.setItem(LAYERS_KEY, JSON.stringify(next))
      } catch {
        /* not remembered - still applied for this visit */
      }
      return next
    })
  }

  // Filters belong to a constituency. Block and area ids are AC-specific, so
  // carrying them across a switch sent another AC's block_id and drew an empty
  // map with no explanation.
  useEffect(() => {
    setBlockId('')
    setAreaId('')
    setElection('VS-2024')
    setSelected(null)
  }, [ac.acNumber])

  const meta = useQuery<{
    blocks: Array<{ block_id: number; name_en: string; name_hi: string }>
    areas: Array<{ area_id: number; block_id: number; name_en: string; name_hi: string }>
    elections: Array<{ label: string; has_results: boolean }>
    contest: { party_a: string; party_b: string } | null
  }>({
    queryKey: ['areas', ac.acNumber],
    queryFn: () => api.get(ac.path('/areas')),
    enabled: ac.acNumber !== null,
  })

  const query = useQuery<Collection>({
    queryKey: ['booths', ac.acNumber, metric, election, blockId, areaId],
    queryFn: () => {
      const params = new URLSearchParams({ metric, election_label: election })
      if (blockId) params.set('block_id', blockId)
      if (areaId) params.set('area_id', areaId)
      return api.get(ac.path(`/booths?${params.toString()}`))
    },
    enabled: ac.acNumber !== null,
    // Without this every filter change was a brand-new query key with no data,
    // so the page unmounted the whole map into a spinner and rebuilt it at the
    // hardcoded centre before refitting. The previous markers now stay up until
    // the filtered set arrives - but only within one constituency, so another
    // AC's booths are never shown under this one's heading.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === ac.acNumber ? keepPreviousData(previous) : undefined,
  })

  // Outlines change only when the constituency does, so they are fetched once
  // per AC rather than with every filter.
  const boundaries = useQuery<Boundaries>({
    queryKey: ['boundaries', ac.acNumber],
    queryFn: () => api.get(ac.path('/boundaries')),
    enabled: ac.acNumber !== null,
    staleTime: Infinity,
  })
  const outline = boundaries.data?.ac ?? null
  const outlineBounds = useMemo<[LatLngTuple, LatLngTuple] | undefined>(() => {
    const box = (outline?.properties as { bbox?: number[] } | undefined)?.bbox
    return box && box.length === 4 ? [[box[1], box[0]], [box[3], box[2]]] : undefined
  }, [outline])
  const hasSynthetic = (boundaries.data?.areas.features ?? []).some((f) => f.properties.synthetic)
  const attribution = Object.values(boundaries.data?.sources ?? {})
    .map((src) => src.attribution).filter(Boolean).join('; ')

  const spec = METRICS[metric]
  const features = query.data?.features ?? []
  const placed = features.filter((f) => f.geometry !== null)
  const contest = query.data?.meta.contest ?? meta.data?.contest ?? null

  /** Radius from the real electorate (F3). A booth with none is a hollow ring. */
  const radiusFor = (electors: unknown) => {
    if (typeof electors !== 'number' || electors <= 0) return 5
    return Math.max(4, Math.min(14, Math.sqrt(electors / 250)))
  }

  const colourFor = (value: unknown): string => {
    if (typeof value !== 'number') return 'var(--text-muted)'
    return spec.diverging
      // F1: the value is already signed by the contest pair, so both arms of
      // the ramp are reachable and mean what the legend says. Item 11: the arms
      // end on that pair's own party colours, matching the legend chips.
      ? divergingPartyColor(
          value, spec.max,
          contest?.party_b ?? 'BJP', contest?.party_a ?? 'JMM',
        )
      : sequentialColor(value, spec.max)
  }

  /** Marker coordinates, for fitBounds. GeoJSON is [lon, lat]; Leaflet wants
   *  [lat, lon], and getting that backwards puts Giridih in the Indian Ocean. */
  const points = useMemo<LatLngTuple[]>(
    () => placed.map((f) => [f.geometry!.coordinates[1], f.geometry!.coordinates[0]]),
    [placed],
  )

  const areasForBlock = useMemo(
    () => (meta.data?.areas ?? []).filter((a) => !blockId || String(a.block_id) === blockId),
    [meta.data, blockId],
  )

  const nullCount = features.filter(
    (f) => typeof f.properties[metric] !== 'number',
  ).length

  if (ac.acNumber === null || (query.isLoading && !query.data)) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  return (
    <div className="space-y-3">
      <FixtureBanner note={query.data?.fixture} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">{t('nav.map')}</h1>
        <div className="flex flex-wrap items-center gap-1.5">
          <select className="select text-2xs" value={metric}
                  onChange={(e) => setMetric(e.target.value as MetricKey)}
                  aria-label={t('common.metric')}>
            {Object.entries(METRICS).map(([key, m]) => (
              <option key={key} value={key}>{t(m.label)}</option>
            ))}
          </select>
          <select className="select text-2xs" value={election}
                  onChange={(e) => setElection(e.target.value)}
                  aria-label={t('common.election')}>
            {(meta.data?.elections ?? [{ label: 'VS-2024', has_results: true }]).map((e) => (
              <option key={e.label} value={e.label} disabled={!e.has_results}>
                {e.label}{!e.has_results ? ` (${t('map.notLoaded')})` : ''}
              </option>
            ))}
          </select>
          <select className="select text-2xs" value={blockId}
                  onChange={(e) => { setBlockId(e.target.value); setAreaId('') }}
                  aria-label={t('common.block')}>
            <option value="">{t('common.allBlocks')}</option>
            {(meta.data?.blocks ?? []).map((b) => (
              <option key={b.block_id} value={b.block_id}>
                {hi ? b.name_hi : b.name_en}
              </option>
            ))}
          </select>
          <select className="select text-2xs" value={areaId}
                  onChange={(e) => setAreaId(e.target.value)}
                  aria-label={t('common.area')}>
            <option value="">{t('common.allAreas')}</option>
            {areasForBlock.map((a) => (
              <option key={a.area_id} value={a.area_id}>
                {hi ? a.name_hi : a.name_en}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Boundary toggle: one switch for all outlines, plus one per layer. */}
      <div className="flex flex-wrap items-center gap-1.5 text-2xs" role="group"
           aria-label={t('map.boundaries')}>
        <button
          type="button"
          className="btn px-2 py-0.5 text-2xs"
          aria-pressed={layers.on}
          onClick={() => updateLayers({ on: !layers.on })}
        >
          {layers.on ? t('map.boundariesOn') : t('map.boundariesOff')}
        </button>
        {(['ac', 'blocks', 'areas'] as const).map((layer) => (
          <label key={layer}
                 className="inline-flex items-center gap-1"
                 style={{ color: layers.on ? 'var(--text-secondary)' : 'var(--text-muted)' }}>
            <input
              type="checkbox"
              checked={layers[layer]}
              disabled={!layers.on}
              onChange={(e) => updateLayers({ [layer]: e.target.checked })}
            />
            {t(layer === 'ac' ? 'map.layerAc' : layer === 'blocks' ? 'map.layerBlocks' : 'map.layerAreas')}
          </label>
        ))}
      </div>

      {tilesFailed && !tileNoticeDismissed && (
        <div
          className="card flex items-start justify-between gap-3 px-3 py-2 text-2xs"
          role="status"
          style={{ color: 'var(--text-secondary)' }}
        >
          <span>{t('map.tilesUnavailable')}</span>
          <button
            className="btn px-2 py-0.5 text-3xs"
            onClick={() => setTileNoticeDismissed(true)}
          >
            {t('common.dismiss')}
          </button>
        </div>
      )}

      {/* A constituency with no booths loaded is said to be empty, rather than
          showing a blank base map that looks like a loading failure. */}
      {/* Booths with results but no location (the real Form 20 load, before a
          PS list with addresses is geocoded) are not "no booths": say how many
          there are and where to read them instead. */}
      {features.length > 0 && placed.length === 0 && (
        <p className="card px-4 py-3 text-sm" role="status" data-testid="map-no-locations">
          {t('map.noLocations', { count: query.data?.meta.ungeocoded ?? 0 })}{' '}
          <Link className="underline" to={`/booths?ac=${ac.acNumber}`}>{t('map.openBoothTable')}</Link>
        </p>
      )}
      {features.length === 0 && (query.data?.meta.ungeocoded ?? 0) === 0 && (
        <p className="card px-4 py-3 text-sm" role="status">{t('overview.noBoothsLoaded')}</p>
      )}

      <div className={`card map-isolate overflow-hidden px-0 py-0${tilesFailed ? ' tiles-failed' : ''}`}>
        <div style={{ height: '62vh' }}>
          <MapContainer
            center={CENTRES[ac.acNumber] ?? CENTRES[32]}
            zoom={11}
            style={{ height: '100%', width: '100%' }}
            scrollWheelZoom
          >
            <BaseTiles onFailure={() => setTilesFailed(true)} />
            <FitBounds
              points={points}
              fallbackBounds={outlineBounds}
              fallback={CENTRES[ac.acNumber] ?? CENTRES[32]}
            />
            {layers.on && boundaries.data && (
              <BoundaryLayers
                data={boundaries.data}
                visible={layers}
                hi={hi}
                blockId={blockId ? Number(blockId) : null}
                areaId={areaId ? Number(areaId) : null}
                unseededLabel={t('map.unseededBlock')}
                syntheticLabel={t('map.syntheticArea')}
                onAreaClick={(area) => setAreaPanel(area)}
              />
            )}
            {placed.map((f) => {
              const p = f.properties
              const value = p[metric]
              const electorsKnown = typeof p.electors === 'number'
              return (
                <CircleMarker
                  key={String(p.booth_uid)}
                  center={[f.geometry!.coordinates[1], f.geometry!.coordinates[0]]}
                  radius={radiusFor(p.electors)}
                  pathOptions={{
                    color: colourFor(value),
                    // F3: an unknown electorate is a hollow ring, not a small
                    // dot - a small dot would read as a small booth.
                    fillOpacity: electorsKnown ? 0.75 : 0,
                    weight: electorsKnown ? 1 : 2,
                    dashArray: electorsKnown ? undefined : '2 2',
                  }}
                  eventHandlers={{ click: () => setSelected(String(p.booth_uid)) }}
                >
                  <LeafletTooltip>
                    <div className="text-2xs">
                      <strong>{String(p.booth_uid)}</strong>
                      {' · '}
                      {String(hi ? p.area_hi : p.area_en)}
                      <br />
                      {t(spec.label)}:{' '}
                      {typeof value === 'number' ? pct(value) : t('map.noValue')}
                      <br />
                      {t('common.electors')}:{' '}
                      {electorsKnown ? num(p.electors as number) : t('map.electorsUnknown')}
                      {p.winner_party ? (
                        <>
                          <br />
                          {t('common.winner')}: {String(p.winner_party)}
                        </>
                      ) : null}
                    </div>
                  </LeafletTooltip>
                </CircleMarker>
              )
            })}
          </MapContainer>
        </div>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          {spec.diverging && contest ? (
            <DivergingLegend
              saturateAt={spec.max}
              leftParty={contest.party_b}
              rightParty={contest.party_a}
              title={t(spec.label)}
            />
          ) : (
            <SequentialLegend max={spec.max} title={t(spec.label)} />
          )}
          {/* The grey entry the audited legend lacked. */}
          {/* The explanation lines were laid out on one flex row, so on a
              narrow card they ran past its edge and the second one was cut
              off entirely. A column of two rows, each wrapping. */}
          <div className="mt-1 flex flex-col gap-1 text-3xs"
               style={{ color: 'var(--text-muted)' }}>
            <span className="flex items-start gap-1.5">
              <span aria-hidden
                    className="mt-[0.2rem] inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ background: 'var(--text-muted)' }} />
              <span className="min-w-0">{t('map.greyMeans')}</span>
            </span>
            <span className="flex items-start gap-1.5">
              <span aria-hidden
                    className="mt-[0.2rem] inline-block h-2.5 w-2.5 shrink-0 rounded-full border-2 border-dashed"
                    style={{ borderColor: 'var(--text-muted)' }} />
              <span className="min-w-0">{t('map.hollowMeans')}</span>
            </span>
          </div>
        </div>

        <ul className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {query.data && query.data.meta.count === 0 && <li>{t('map.noBooths')}</li>}
          {layers.on && boundaries.data && (
            <>
              {attribution && <li>{t('map.boundaryNote', { sources: attribution })}</li>}
              {boundaries.data.warnings.map((w) => (
                <li key={w.code + JSON.stringify(w.params ?? {})} style={{ color: 'var(--status-serious)' }}>
                  {t(`map.warn_${w.code}`, { ...w.params, defaultValue: w.message })}
                </li>
              ))}
              {layers.areas && hasSynthetic && <li>{t('map.syntheticNote')}</li>}
              {layers.areas && boundaries.data.areas.features.length === 0 && (
                <li>{t('map.noAreaShapes')}</li>
              )}
            </>
          )}
          <li>{t('map.sizeNote')}</li>
          {query.data && query.data.meta.ungeocoded > 0 && (
            <li>
              {t('map.ungeocoded', { count: query.data.meta.ungeocoded })}
              {' '}
              <code className="whitespace-pre-wrap break-all">
                python -m ingest.geocode
              </code>
            </li>
          )}
          {nullCount > 0 && (
            <li>{t('map.nullCount', { count: nullCount, metric: t(spec.label) })}</li>
          )}
          {query.data && query.data.meta.electors_known < query.data.meta.count && (
            <li>
              {t('map.electorsPartial', {
                known: query.data.meta.electors_known,
                total: query.data.meta.count,
              })}
            </li>
          )}
        </ul>
      </div>

      {selected && (
        <BoothDrawer boothUid={selected} ac={ac} onClose={() => setSelected(null)} />
      )}
      {areaPanel?.area_id && !areaPanel.synthetic && (
        <AreaNewsDrawer ac={ac} area={{ area_id: areaPanel.area_id, name_en: areaPanel.name_en,
                                        name_hi: areaPanel.name_hi }}
                        election={election} onClose={() => setAreaPanel(null)} />
      )}
    </div>
  )
}
