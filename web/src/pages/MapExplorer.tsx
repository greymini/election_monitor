import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CircleMarker, MapContainer, TileLayer, Tooltip as LeafletTooltip } from 'react-leaflet'

import BoothDrawer from '../components/BoothDrawer'
import DivergingLegend from '../components/DivergingLegend'
import SequentialLegend from '../components/SequentialLegend'
import { FixtureBanner } from '../components/Provenance'
import { ErrorState, Loading } from '../components/States'
import type { AcState } from '../lib/ac'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'
import { divergingColor, sequentialColor } from '../lib/tokens'

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

export default function MapExplorer({ ac }: Props) {
  const { t, i18n } = useTranslation()
  const hi = i18n.language === 'hi'
  const [metric, setMetric] = useState<MetricKey>('signed_margin_pct')
  const [election, setElection] = useState('VS-2024')
  const [blockId, setBlockId] = useState<string>('')
  const [areaId, setAreaId] = useState<string>('')
  const [selected, setSelected] = useState<string | null>(null)

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
  })

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
      // the ramp are reachable and mean what the legend says.
      ? divergingColor(value, spec.max)
      : sequentialColor(value, spec.max)
  }

  const areasForBlock = useMemo(
    () => (meta.data?.areas ?? []).filter((a) => !blockId || String(a.block_id) === blockId),
    [meta.data, blockId],
  )

  const nullCount = features.filter(
    (f) => typeof f.properties[metric] !== 'number',
  ).length

  if (ac.acNumber === null || query.isLoading) return <Loading />
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
            <option value="">{t('common.all')} {t('common.block')}</option>
            {(meta.data?.blocks ?? []).map((b) => (
              <option key={b.block_id} value={b.block_id}>
                {hi ? b.name_hi : b.name_en}
              </option>
            ))}
          </select>
          <select className="select text-2xs" value={areaId}
                  onChange={(e) => setAreaId(e.target.value)}
                  aria-label={t('common.area')}>
            <option value="">{t('common.all')} {t('common.area')}</option>
            {areasForBlock.map((a) => (
              <option key={a.area_id} value={a.area_id}>
                {hi ? a.name_hi : a.name_en}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card overflow-hidden px-0 py-0">
        <div style={{ height: '62vh' }}>
          <MapContainer
            center={CENTRES[ac.acNumber] ?? CENTRES[32]}
            zoom={11}
            style={{ height: '100%', width: '100%' }}
            scrollWheelZoom
          >
            <TileLayer
              attribution='&copy; OpenStreetMap, &copy; CARTO'
              url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
            />
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
          <div className="mt-1 flex items-center gap-1.5 text-3xs"
               style={{ color: 'var(--text-muted)' }}>
            <span className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: 'var(--text-muted)' }} />
            {t('map.greyMeans')}
            <span className="ml-3 inline-block h-2.5 w-2.5 rounded-full border-2 border-dashed"
                  style={{ borderColor: 'var(--text-muted)' }} />
            {t('map.hollowMeans')}
          </div>
        </div>

        <ul className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          <li>{t('map.sizeNote')}</li>
          {query.data && query.data.meta.ungeocoded > 0 && (
            <li>
              {t('map.ungeocoded', { n: query.data.meta.ungeocoded })}
              {' '}
              <code>python -m ingest.geocode --ac {ac.acNumber}</code>
            </li>
          )}
          {nullCount > 0 && (
            <li>{t('map.nullCount', { n: nullCount, metric: t(spec.label) })}</li>
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
    </div>
  )
}
