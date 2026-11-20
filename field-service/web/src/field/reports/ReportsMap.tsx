/** Reports map: Leaflet with the configurable tile URL (MAP_TILE_URL, from /places), pins coloured by
 * category with a legend that names them, and a count of the reports that have no location. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { CircleMarker, MapContainer, TileLayer, Tooltip as LTooltip } from "react-leaflet";
import { Async, Page, Panel, PillTabs } from "@/components/Layout";
import { ValueLegend } from "@/components/charts";
import { num } from "@/lib/format";
import { categoryColor, isFoldedCategory, OTHER } from "@/lib/palette";
import { acLabel, useSession } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import { useNamer, usePlaces } from "../data";
import { useCategories, useReports } from "./data";
import { ReportDrawer } from "./ReportDrawer";

export function ReportsMap() {
  const { t } = useTranslation();
  const s = useSession();
  const name = useNamer();
  const { color } = useTheme();
  const places = usePlaces();
  const cats = useCategories();
  const [view, setView] = useState<"open" | "all">("open");
  const q = useReports({ located: true, ...(view === "open" ? { open: true } : {}) });
  const [openId, setOpenId] = useState<string | null>(null);
  const pinColor = (code: string | null) => (code && !isFoldedCategory(code) ? categoryColor(code) : OTHER);

  return (
    <Page title={t("nav.field.map")} qualifier={`${s.acNumber} · ${acLabel(s)}`}
      actions={<PillTabs label={t("nav.field.map")} value={view} onChange={setView}
        items={[{ id: "open", label: t("reports.view_open") }, { id: "all", label: t("reports.view_all") }]} />}>
      <Async q={q}>
        {(d) => {
          const pts = d.items.filter((r) => r.lat && r.lon);
          const lat = pts.length ? pts.reduce((a, r) => a + Number(r.lat), 0) / pts.length : 24.19;
          const lon = pts.length ? pts.reduce((a, r) => a + Number(r.lon), 0) / pts.length : 86.3;
          const legend = new Map<string, { key: string; label: string; value: number; color: string }>();
          for (const r of pts) {
            const key = r.category_code ?? "unsorted";
            const e = legend.get(key) ?? { key, label: r.category_id === null ? t("reports.unsorted") : name(cats.get(r.category_id), r.category_name_hi ?? ""),
              value: 0, color: pinColor(r.category_code) };
            e.value += 1;
            legend.set(key, e);
          }
          return (
            <>
              <p className="text-note text-muted" data-testid="map-counts">
                {t("map.counts", { shown: num(pts.length), missing: num(d.without_location ?? 0) })}
              </p>
              <Panel>
                <div className="overflow-hidden rounded-ctl" style={{ height: 520 }} data-testid="map">
                  <MapContainer center={[lat, lon]} zoom={11} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
                    <TileLayer url={places.data?.map.tile_url ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
                      attribution={places.data?.map.attribution ?? "© OpenStreetMap contributors"} />
                    {pts.map((r) => (
                      <CircleMarker key={r.report_id} center={[Number(r.lat), Number(r.lon)]} radius={r.urgency === "urgent" ? 10 : 7}
                        pathOptions={{ color: color("var(--em-surface)"), weight: 2, fillColor: color(pinColor(r.category_code)), fillOpacity: 1 }}
                        eventHandlers={{ click: () => setOpenId(r.report_id) }}>
                        <LTooltip>{r.report_no} · {r.title ?? ""}{r.urgency === "urgent" ? ` · ${t("reports.urgent")}` : ""}</LTooltip>
                      </CircleMarker>
                    ))}
                  </MapContainer>
                </div>
                <div className="mt-3"><ValueLegend items={[...legend.values()]} format={(v) => num(v)} /></div>
                <p className="mt-2 text-note text-muted">{t("map.note")}</p>
              </Panel>
            </>
          );
        }}
      </Async>
      <ReportDrawer reportId={openId} onClose={() => setOpenId(null)} />
    </Page>
  );
}
