import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CircleMarker, MapContainer, TileLayer, Tooltip as LeafletTooltip } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

import BoothDrawer from '../components/BoothDrawer'
import DivergingLegend from '../components/DivergingLegend'
import SequentialLegend from '../components/SequentialLegend'
import { ErrorState, Loading } from '../components/States'
import { api } from '../lib/api'
import { num, pct } from '../lib/format'
import { divergingColor, sequentialColor, token } from '../lib/tokens'

const GIRIDIH_CENTRE: [number, number] = [24.1854, 86.3094]

const METRICS = [
  { key: 'margin_pct', diverging: true, max: 20 },
  { key: 'turnout_pct', diverging: false, max: 100 },
  { key: 'new_voter_pct', diverging: false, max: 25 },
  { key: 'priority_score', diverging: false, max: 100 },
  { key: 'floating_pct', diverging: false, max: 40 },
] as const

interface Feature {
  geometry: { type: 'Point'; coordinates: [number, number] } | null
  properties: {
    booth_uid: string; building: string | null; village_or_locality: string | null
    area_hi: string; area_en: string; block_id: number
    metric: number | null; metric_name: string
    winner_party: string | null; runner_party: string | null
    margin_pct: number | null; electors: number | null
  }
}

export default function MapExplorer() {
  const { t, i18n } = useTranslation()
  const [metric, setMetric] = useState<string>('margin_pct')
  const [selected, setSelected] = useState<string | null>(null)

  const query = useQuery<{ features: Feature[]; meta: { count: number; ungeocoded: number } }>({
    queryKey: ['booths', metric],
    queryFn: () => api.get(`/booths?metric=${metric}`),
  })

  const spec = METRICS.find((m) => m.key === metric)!
  const placed = useMemo(
    () => (query.data?.features ?? []).filter((f) => f.geometry),
    [query.data],
  )

  const colourFor = (value: number | null) =>
    spec.diverging
      // margin_pct is unsigned in the view; sign it by who won so the ramp can
      // read as "JMM lead" on one side and "BJP lead" on the other.
      ? divergingColor(value, spec.max)
      : sequentialColor(value, spec.max)

  if (query.isLoading) return <Loading />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">{t('map.heading')}</h1>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {t('common.metric')}
          </label>
          <select className="field" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m.key} value={m.key}>{m.key.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </div>
      </div>

      {spec.diverging ? (
        <DivergingLegend saturateAt={spec.max} />
      ) : (
        <SequentialLegend title={metric.replace(/_/g, ' ')} max={spec.max} />
      )}

      {query.data && query.data.meta.ungeocoded > 0 && (
        <p className="text-2xs" style={{ color: 'var(--status-warning)' }}>
          ! {t('map.ungeocoded', { count: query.data.meta.ungeocoded })} — run{' '}
          <code>python -m ingest.geocode</code> or pin them in the admin view.
        </p>
      )}

      <div className="card overflow-hidden" style={{ height: '60vh', minHeight: 380 }}>
        <MapContainer center={GIRIDIH_CENTRE} zoom={11} scrollWheelZoom
                      style={{ height: '100%', width: '100%' }}>
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; OpenStreetMap contributors &copy; CARTO'
          />
          {placed.map((feature) => {
            const p = feature.properties
            const [lon, lat] = feature.geometry!.coordinates
            // Radius carries electorate size; colour carries the chosen metric.
            const radius = Math.max(4, Math.min(11, Math.sqrt((p.electors ?? 400) / 40)))
            return (
              <CircleMarker
                key={p.booth_uid}
                center={[lat, lon]}
                radius={radius}
                pathOptions={{
                  color: token('--surface-1'),
                  weight: 2,
                  fillColor: colourFor(p.metric),
                  fillOpacity: 0.92,
                }}
                eventHandlers={{ click: () => setSelected(p.booth_uid) }}
              >
                <LeafletTooltip direction="top" offset={[0, -4]}>
                  <div className="text-2xs">
                    <div className="font-semibold">{p.booth_uid}</div>
                    <div>{p.building ?? p.village_or_locality ?? '—'}</div>
                    <div style={{ color: 'var(--text-muted)' }}>
                      {i18n.language === 'hi' ? p.area_hi : p.area_en}
                    </div>
                    <div className="tnum mt-1">
                      {p.metric_name.replace(/_/g, ' ')}: {p.metric === null ? '—' : pct(p.metric)}
                    </div>
                    <div className="tnum">
                      {p.winner_party ?? '—'} over {p.runner_party ?? '—'} ·{' '}
                      {num(p.electors)} electors
                    </div>
                  </div>
                </LeafletTooltip>
              </CircleMarker>
            )
          })}
        </MapContainer>
      </div>

      <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
        {t('map.clickBooth')} · Marker size is the electorate; colour is the selected metric.
      </p>

      {selected && <BoothDrawer boothUid={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
