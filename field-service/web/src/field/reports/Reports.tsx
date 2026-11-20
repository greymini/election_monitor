/** Reports: KPI cards, category donut, bars by area, weekly trend, and the inbox as cards ordered
 * urgent, unsorted, oldest. A card opens the report drawer. Restricted reports are marked; the API
 * leaves out the ones the viewer may not see. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Async, Grid, KpiCard, KpiRow, Page, Panel, PillTabs } from "@/components/Layout";
import { Chip, Dot, MachineLabel, StatusChip } from "@/components/Values";
import { ChartFrame, Donut, HBars, StackedBars, ValueLegend, type Slice } from "@/components/charts";
import { date, num } from "@/lib/format";
import { categoryColor, isFoldedCategory, OTHER } from "@/lib/palette";
import { acLabel, useSession } from "@/lib/session";
import { useAreaIndex, useNamer } from "../data";
import { SimpleTable } from "../spend/SpendOverview";
import { age, useCategories, useReports, useReportSummary, type ReportRow } from "./data";
import { ReportDrawer } from "./ReportDrawer";

type View = "open" | "all";

export function Reports() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const name = useNamer();
  const places = useAreaIndex();
  const summary = useReportSummary();
  const cats = useCategories();
  const [view, setView] = useState<View>("open");
  const list = useReports(view === "open" ? { open: true } : {});
  const [sp, setSp] = useSearchParams();
  const openId = sp.get("open");
  const setOpen = (id: string | null) => setSp((cur) => { const n = new URLSearchParams(cur); if (id) n.set("open", id); else n.delete("open"); return n; }, { replace: true });

  const d = summary.data;
  const catLabel = (id: number | null) => (id === null ? t("reports.unsorted") : name(cats.get(id), String(id)));
  const catCode = (id: number | null) => (id === null ? null : cats.get(id)?.code ?? null);
  // the donut folds the categories without a slot into "Other"; the legend keeps a row per category
  const slices: Slice[] = (() => {
    const out: Slice[] = [];
    let other = 0;
    for (const c of d?.by_category ?? []) {
      const code = catCode(c.category_id);
      if (c.category_id !== null && isFoldedCategory(code)) other += c.n;
      else out.push({ key: String(c.category_id), label: catLabel(c.category_id), value: c.n,
        color: c.category_id === null ? OTHER : categoryColor(code) });
    }
    if (other) out.push({ key: "other", label: t("reports.other_categories"), value: other, color: OTHER });
    return out;
  })();
  const total = slices.reduce((a, x) => a + x.value, 0);
  const weekMap = new Map<string, Record<string, number | string>>();
  const seriesKeys = new Set<string>();
  for (const r of d?.by_week_category ?? []) {
    const code = catCode(r.category_id);
    const key = r.category_id === null ? "unsorted" : isFoldedCategory(code) ? "other" : String(r.category_id);
    seriesKeys.add(key);
    const row = weekMap.get(r.week) ?? { week: r.week };
    row[key] = Number(row[key] ?? 0) + r.n;
    weekMap.set(r.week, row);
  }
  const weekRows = [...weekMap.values()].sort((a, b) => String(a.week).localeCompare(String(b.week)));
  const series = slices.filter((x) => seriesKeys.has(x.key === "null" ? "unsorted" : x.key))
    .map((x) => ({ key: x.key === "null" ? "unsorted" : x.key, label: x.label, color: x.color }));
  const areaBars = (d?.by_area ?? []).map((a) => ({ key: a.area_id ?? "none",
    label: a.area_id ? name({ name_hi: a.name_hi ?? a.area_id, name_en: a.name_en ?? a.area_id }) : t("spend.no_area"), value: a.n }));

  return (
    <Page title={t("nav.field.reports")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <KpiRow>
        <KpiCard testId="kpi-urgent" label={t("reports.kpi.urgent")} value={d ? num(d.open.urgent) : undefined} sub={t("reports.kpi.urgent_sub")} />
        <KpiCard label={t("reports.kpi.unsorted")} value={d ? num(d.open.unsorted) : undefined} sub={t("reports.kpi.unsorted_sub")} />
        <KpiCard label={t("reports.kpi.ack")} value={d?.medians.ack_minutes != null ? t("reports.kpi.minutes", { n: num(d.medians.ack_minutes, 1) }) : null}
          missingReason={t("reports.kpi.no_ack")} sub={t("reports.kpi.ack_sub")} />
        <KpiCard label={t("reports.kpi.resolve")} value={d?.medians.resolve_days != null ? t("reports.kpi.days", { n: num(d.medians.resolve_days, 1) }) : null}
          missingReason={t("reports.kpi.no_resolve")} sub={t("reports.kpi.resolve_sub")} />
      </KpiRow>
      <Grid cols={3}>
        <Panel title={t("reports.by_category")} subtitle={t("reports.by_category_sub")}>
          <Async q={summary} empty={(x) => !x.by_category.length}>
            {() => (
              <ChartFrame height={220} legend={<ValueLegend columns={1} items={slices} total={total} format={(v) => num(v)} />}
                table={<SimpleTable head={[t("reports.category"), t("reports.count")]}
                  rows={(d?.by_category ?? []).map((c) => [catLabel(c.category_id), num(c.n)])} />}>
                <Donut data={slices} centerLabel={t("reports.total")} valueFormat={(v) => num(v)} />
              </ChartFrame>
            )}
          </Async>
        </Panel>
        <Panel title={t("reports.by_area")} subtitle={t("reports.by_area_sub")}>
          <Async q={summary} empty={(x) => !x.by_area.length}>{() => <HBars items={areaBars} format={(v) => num(v)} />}</Async>
        </Panel>
        <Panel title={t("reports.weekly")} subtitle={t("reports.weekly_sub")}>
          <Async q={summary} empty={(x) => !x.by_week.length}>
            {() => (
              <ChartFrame height={220} legend={<ValueLegend columns={1} format={(v) => num(v)}
                items={series.map((x) => ({ ...x, value: weekRows.reduce((a, r) => a + Number(r[x.key] ?? 0), 0) }))} />}
                table={<SimpleTable head={[t("spend.col.week"), t("reports.count")]} rows={(d?.by_week ?? []).map((w) => [date(w.week, i18n.language), num(w.n)])} />}>
                <StackedBars rows={weekRows} series={series} xKey="week" yFormat={(v) => num(v) ?? ""}
                  xFormat={(w) => date(w, i18n.language) ?? w} />
              </ChartFrame>
            )}
          </Async>
        </Panel>
      </Grid>
      <Panel title={t("reports.inbox")} subtitle={t("reports.inbox_sub")}
        action={<PillTabs label={t("reports.inbox")} value={view} onChange={setView}
          items={[{ id: "open", label: t("reports.view_open") }, { id: "all", label: t("reports.view_all") }]} />}>
        <Async q={list} empty={(x) => !x.items.length}>
          {(x) => (
            <ul className="grid gap-gap [grid-template-columns:repeat(auto-fill,minmax(340px,1fr))]" data-testid="inbox">
              {x.items.map((r) => <Card key={r.report_id} r={r} onOpen={() => setOpen(r.report_id)}
                areaName={r.area_id ? name(places.area.get(r.area_id), r.area_id) : null} catLabel={catLabel(r.category_id)} />)}
            </ul>
          )}
        </Async>
      </Panel>
      <ReportDrawer reportId={openId} onClose={() => setOpen(null)} />
    </Page>
  );
}

function Card({ r, onOpen, areaName, catLabel }: { r: ReportRow; onOpen: () => void; areaName: string | null; catLabel: string }) {
  const { t } = useTranslation();
  return (
    <li>
      <button type="button" onClick={onOpen} data-testid="report-card" data-urgent={r.urgency === "urgent"}
        className={`em-card flex h-full w-full gap-3 p-3 text-left hover:bg-raised ${r.urgency === "urgent" ? "border-bad/70" : ""}`}>
        <div className="h-24 w-24 shrink-0 overflow-hidden rounded-ctl bg-raised">
          {r.thumb_url ? <img src={r.thumb_url} alt={t("reports.photo")} className="h-full w-full object-cover" loading="lazy" />
            : <div className="flex h-full items-center justify-center text-label text-muted">{r.voice_count ? `🎙 ${t("reports.voice")}` : t("reports.no_photo")}</div>}
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            {r.urgency === "urgent" ? <Chip tone="bad" icon="●">{t("reports.urgent")}</Chip> : null}
            <StatusChip status={r.status} kind="report" />
            {r.restricted ? <Chip tone="serious" icon="🔒">{t("reports.restricted")}</Chip> : null}
          </div>
          <div className="truncate font-semibold" title={r.title ?? undefined}>
            {r.title ?? <span className="text-muted">{r.report_no}</span>}
          </div>
          {r.title_source === "auto" ? <div className="mb-0.5"><MachineLabel what={t("reports.auto_title")} /></div> : null}
          <div className="flex items-center gap-1.5 text-note">
            <Dot color={r.category_id === null ? OTHER : categoryColor(r.category_code)} />
            <span className={r.category_id === null ? "text-warn" : "text-ink-2"}>{catLabel}</span>
          </div>
          <div className="truncate text-note text-muted">
            {r.report_no} · {areaName ?? t("spend.no_area")} · {r.reporter_name} · {age(r.filed_at, t)}
          </div>
        </div>
      </button>
    </li>
  );
}
