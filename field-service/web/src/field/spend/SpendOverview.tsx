/** Spend overview (LLD §10 Spend summary). KPI cards, donut by bucket (click to drill into sub-heads
 * and filter the page), weekly stacked bars, bars by area and member, the tail panel, pay mode and
 * fund source donuts, and need versus spend ("association, not cause"). */
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Async, Grid, KpiCard, KpiRow, Page, Panel } from "@/components/Layout";
import { EstimateLabel } from "@/components/Values";
import { ChartFrame, Donut, HBars, LabelledScatter, PairedBars, StackedBars, ValueLegend, type Slice } from "@/components/charts";
import { DataTable } from "@/components/DataTable";
import { useApi } from "@/lib/api";
import { date, inr, num, pct, toNumber, todayIst } from "@/lib/format";
import { bucketColor, OTHER, slot, subheadColor } from "@/lib/palette";
import { atLeast, useSession, acLabel } from "@/lib/session";
import { useAreaIndex, useBalances, useBucketIndex, useNamer, useSpendFilters, type Summary } from "../data";
import { SpendFilterRow } from "./Filters";

function useSummary(groupBy: string, params: Record<string, string | null>, enabled = true) {
  const api = useApi();
  const s = useSession();
  return useQuery({
    queryKey: ["summary", s.userId, groupBy, params], enabled,
    queryFn: () => api.get<Summary>("/spend/summary", { group_by: groupBy, ...params }),
  });
}

const PAY_SLOTS: Record<string, number> = { cash: 3, upi: 2, bank_transfer: 6, cheque: 1, credit: 5, in_kind: 8 };
const FUND_SLOTS: Record<string, number> = { own: 3, family_business: 1, party: 2, supporter_cash: 5, supporter_in_kind: 8,
  advance: 6, own_pocket_reimbursable: 4, other: 12 };

export function SpendOverview() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const api = useApi();
  const name = useNamer();
  const { f, set, params } = useSpendFilters();
  const buckets = useBucketIndex();
  const places = useAreaIndex();
  const all = atLeast(s, "all");
  const drilled = f.bucket ? buckets.byId.get(Number(f.bucket)) : undefined;

  const byBucket = useSummary(drilled ? "sub_bucket" : "bucket", params);
  const byWeek = useSummary("week,bucket", params);
  const byArea = useSummary("area", params);
  const byMember = useSummary("member", params);
  const byPay = useSummary("pay_mode", params);
  const byFund = useSummary("fund_source", params);
  const balances = useBalances();
  const tail = useQuery({ queryKey: ["tail", s.userId, params], enabled: all,
    queryFn: () => api.get<{ bands: { band: string; count: number; value: string }[]; entries: number; spend_total: string;
      tail_threshold: string }>("/spend/tail", params) });
  const nvs = useQuery({ queryKey: ["nvs", s.userId, params.date_from, params.date_to], enabled: all,
    queryFn: () => api.get<{ areas: NvsArea[] }>("/spend/need-vs-spend", { date_from: params.date_from, date_to: params.date_to }) });

  const totals = byBucket.data?.totals;
  const position = balances.data?.team_position;                     // server figure; not netted
  const today = todayIst();
  const window = byBucket.data?.ceiling.find((c) => c.starts_on <= today && today <= c.ends_on);

  const bucketSlices: Slice[] = (byBucket.data?.groups ?? []).map((g) => {
    if (drilled) {
      const sub = buckets.byId.get(Number(g.sub_bucket));
      const isTop = sub?.parent_id === null;
      return { key: String(g.sub_bucket), label: isTop ? t("spend.no_subhead") : name(sub, String(g.sub_bucket_code)),
        value: toNumber(g.spend) ?? 0, color: isTop ? OTHER : subheadColor(sub?.sortIndex ?? -1) };
    }
    const b = buckets.byId.get(Number(g.bucket));
    return { key: String(g.bucket), label: name(b, String(g.bucket_name_hi)), value: toNumber(g.spend) ?? 0,
      color: bucketColor(String(g.bucket_code)) };
  });
  const sliceTotal = bucketSlices.reduce((a, x) => a + x.value, 0);

  // weekly stacked bars: one series per top-level bucket, in the buckets' fixed order
  const weeks = new Map<string, Record<string, number | string>>();
  const present = new Set<string>();
  for (const g of byWeek.data?.groups ?? []) {
    const wk = String(g.week);
    const code = String(g.bucket_code);
    present.add(code);
    const row = weeks.get(wk) ?? { week: wk };
    row[code] = (Number(row[code] ?? 0)) + (toNumber(g.spend) ?? 0);
    weeks.set(wk, row);
  }
  const weekRows = [...weeks.values()].sort((a, b) => String(a.week).localeCompare(String(b.week)));
  const series = buckets.tops.filter((b) => present.has(b.code)).map((b) => ({ key: b.code, label: name(b), color: bucketColor(b.code) }));

  const areaBars = (byArea.data?.groups ?? []).map((g) => ({
    key: String(g.area ?? "none"), label: g.area ? name(places.area.get(String(g.area)), String(g.area)) : t("spend.no_area"),
    value: toNumber(g.spend) ?? 0, sub: `${num(g.entries)} ${t("spend.entries_word")}`,
  }));
  const memberBars = (byMember.data?.groups ?? []).map((g) => ({
    key: String(g.member), label: String(g.member_name ?? t("common.unknown")), value: toNumber(g.spend) ?? 0,
    sub: `${num(g.entries)} ${t("spend.entries_word")}`,
  }));
  const small = (data: Summary | undefined, key: string, slots: Record<string, number>, prefix: string): Slice[] =>
    (data?.groups ?? []).map((g) => {
      const v = g[key] === null ? "none" : String(g[key]);
      return { key: v, label: v === "none" ? t("spend.not_recorded") : t(`${prefix}.${v}`), value: toNumber(g.spend) ?? 0,
        color: slots[v] ? slot(slots[v]) : OTHER };
    });
  const paySlices = small(byPay.data, "pay_mode", PAY_SLOTS, "pay_mode");
  const fundSlices = small(byFund.data, "fund_source", FUND_SLOTS, "fund_source");

  const tailRows = (() => {
    const d = tail.data;
    if (!d) return [];
    const n = d.entries || 0, v = toNumber(d.spend_total) || 0;
    return d.bands.map((b) => ({ key: b.band, label: t(`band.${b.band}`),
      a: n ? (b.count / n) * 100 : 0, b: v ? ((toNumber(b.value) ?? 0) / v) * 100 : 0, count: b.count, value: b.value }));
  })();

  return (
    <Page title={t("nav.field.spend")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <SpendFilterRow />
      {drilled ? (
        <div className="flex items-center gap-2 text-note" data-testid="drill-crumb">
          <button type="button" className="em-link" onClick={() => set({ bucket: "" })}>{t("spend.all_buckets")}</button>
          <span className="text-muted">›</span>
          <span className="font-semibold text-ink">{name(drilled)}</span>
        </div>
      ) : null}
      <KpiRow>
        <KpiCard testId="kpi-spend" label={t("spend.kpi.spend")} value={inr(totals?.spend_total)}
          sub={totals ? t("spend.kpi.spend_sub", { n: num(totals.entries) }) : undefined} />
        <KpiCard label={t("spend.kpi.held")} value={position ? inr(position.holds) : undefined} testId="kpi-held"
          sub={position && (toNumber(position.owed) ?? 0) > 0 ? t("spend.kpi.held_owed", { owed: inr(position.owed) }) : t("spend.kpi.held_sub")}
          missingReason={t("spend.kpi.held_missing")} />
        <KpiCard label={t("spend.kpi.in_transit")} value={inr(totals?.in_transit)} sub={t("spend.kpi.in_transit_sub")} />
        <KpiCard label={t("spend.kpi.disputed")} value={inr(totals?.disputed)} sub={t("spend.kpi.disputed_sub")} />
        <KpiCard label={t("spend.kpi.unclassified")} value={pct(totals?.unclassified_pct)}
          missingReason={t("spend.kpi.no_spend")} sub={t("spend.kpi.unclassified_sub")} />
        {window ? (
          <KpiCard testId="kpi-ceiling" label={t("spend.kpi.ceiling")} value={pct(window.ceiling_used_pct)}
            missingReason={t("register.no_ceiling")}
            sub={t("spend.kpi.ceiling_sub", { counted: inr(window.counted), ceiling: inr(window.ceiling_amount) ?? "—" })} />
        ) : null}
      </KpiRow>

      <Grid cols={2}>
        <Panel testId="panel-bucket-donut" title={drilled ? t("spend.by_subhead", { bucket: name(drilled) }) : t("spend.by_bucket")}
          subtitle={drilled ? t("spend.by_subhead_sub") : t("spend.by_bucket_sub")}>
          <Async q={byBucket} empty={(d) => !d.groups.length}>
            {() => (
              <ChartFrame height={250}
                legend={<ValueLegend items={bucketSlices} total={sliceTotal}
                  onSelect={drilled ? undefined : (k) => set({ bucket: k })} />}
                table={<SimpleTable rows={bucketSlices.map((x) => [x.label, inr(x.value), pct((x.value / (sliceTotal || 1)) * 100)])}
                  head={[t("spend.col.bucket"), t("spend.col.spend"), t("spend.col.share")]} />}>
                <Donut data={bucketSlices} centerLabel={t("spend.total")} onSelect={drilled ? undefined : (k) => set({ bucket: k })} />
              </ChartFrame>
            )}
          </Async>
        </Panel>
        <Panel title={t("spend.weekly")} subtitle={t("spend.weekly_sub")}>
          <Async q={byWeek} empty={(d) => !d.groups.length}>
            {() => (
              <ChartFrame height={300}
                legend={<ValueLegend items={series.map((x) => ({ ...x, value: weekRows.reduce((a, r) => a + Number(r[x.key] ?? 0), 0) }))} />}
                table={<SimpleTable head={[t("spend.col.week"), ...series.map((x) => x.label)]}
                  rows={weekRows.map((r) => [date(String(r.week), i18n.language), ...series.map((x) => inr(Number(r[x.key] ?? 0)))])} />}>
                <StackedBars rows={weekRows} series={series} xKey="week"
                  xFormat={(w) => t("spend.week_of", { d: date(w, i18n.language) })} />
              </ChartFrame>
            )}
          </Async>
        </Panel>
        <Panel title={t("spend.by_area")} subtitle={t("spend.by_area_sub")}>
          <Async q={byArea} empty={(d) => !d.groups.length}>
            {() => <HBars items={areaBars} onSelect={(k) => k !== "none" && set({ area: k })} testId="bars-area" />}
          </Async>
        </Panel>
        <Panel title={t("spend.by_member")} subtitle={t("spend.by_member_sub")}>
          <Async q={byMember} empty={(d) => !d.groups.length}>
            {() => <HBars items={memberBars} onSelect={(k) => set({ member: k })} />}
          </Async>
        </Panel>
        {all ? (
          <Panel title={t("spend.tail")} subtitle={t("spend.tail_sub", { threshold: inr(tail.data?.tail_threshold) ?? "—" })}
            footnote={totals ? t("spend.tail_note", { entries: pct(totals.tail_count_pct) ?? "—", value: pct(totals.tail_value_pct) ?? "—",
              top: pct(totals.top_decile_value_pct) ?? "—" }) : undefined}>
            <Async q={tail} empty={(d) => !d.entries}>
              {() => (
                <ChartFrame height={tailRows.length * 52 + 40}
                  table={<SimpleTable head={[t("spend.col.band"), t("spend.col.entries"), t("spend.col.spend"), t("chart.share_entries"), t("chart.share_value")]}
                    rows={tailRows.map((r) => [r.label, num(r.count), inr(r.value), pct(r.a), pct(r.b)])} />}>
                  <PairedBars rows={tailRows} />
                </ChartFrame>
              )}
            </Async>
          </Panel>
        ) : null}
        <Panel title={t("spend.pay_and_fund")} subtitle={t("spend.pay_and_fund_sub")}>
          <div className="grid gap-6 sm:grid-cols-2">
            {[{ title: t("spend.by_pay_mode"), data: paySlices, q: byPay }, { title: t("spend.by_fund_source"), data: fundSlices, q: byFund }].map((x) => (
              <div key={x.title}>
                <h3 className="mb-2 text-body font-semibold">{x.title}</h3>
                <Async q={x.q} empty={(d) => !d.groups.length}>
                  {() => (
                    <ChartFrame height={150} legend={<ValueLegend columns={1} items={x.data} total={x.data.reduce((a, v) => a + v.value, 0)} />}
                      table={<SimpleTable head={[x.title, t("spend.col.spend")]} rows={x.data.map((d) => [d.label, inr(d.value)])} />}>
                      <Donut data={x.data} size="sm" />
                    </ChartFrame>
                  )}
                </Async>
              </div>
            ))}
          </div>
        </Panel>
      </Grid>

      {all ? <NeedVsSpend q={nvs} /> : null}
    </Page>
  );
}

interface NvsArea {
  area_id: string; name_hi: string | null; name_en: string | null; electors: number | null; open_reports: number;
  open_issues: number; spend: string; need_per_1000: string | null; spend_per_1000: string | null;
  need_rank: string | null; spend_rank: string | null; rank_gap: string | null;
}

function NeedVsSpend({ q }: { q: ReturnType<typeof useQuery<{ areas: NvsArea[] }>> }) {
  const { t } = useTranslation();
  const name = useNamer();
  return (
    <Panel testId="panel-nvs" title={<span className="flex items-center gap-2">{t("nvs.title")} <EstimateLabel /></span>}
      subtitle={t("nvs.sub")} footnote={<span data-testid="nvs-label">{t("nvs.association")}</span>}>
      <Async q={q} empty={(d) => !d.areas.length}>
        {(d) => {
          const pts = d.areas.filter((a) => a.need_per_1000 !== null && a.spend_per_1000 !== null).map((a) => ({
            key: a.area_id, label: name({ name_hi: a.name_hi ?? a.area_id, name_en: a.name_en ?? a.area_id }),
            x: toNumber(a.need_per_1000) ?? 0, y: toNumber(a.spend_per_1000) ?? 0,
          }));
          return (
            <div className="grid gap-5 xl:grid-cols-[1fr_1.2fr]">
              <div style={{ height: 320 }}>
                <LabelledScatter points={pts} xLabel={t("nvs.need_axis")} yLabel={t("nvs.spend_axis")} />
              </div>
              <DataTable filters={false} rowKey={(a) => a.area_id} rows={d.areas}
                initialSort={[{ key: "gap", dir: -1 }]}
                columns={[
                  { key: "area", header: t("nvs.col.area"), render: (a) => name({ name_hi: a.name_hi ?? a.area_id, name_en: a.name_en ?? a.area_id }), sort: (a) => a.name_hi },
                  { key: "electors", header: t("nvs.col.electors"), align: "right", render: (a) => a.electors === null ? <span className="text-muted" title={t("nvs.no_electors")}>—</span> : num(a.electors), sort: (a) => a.electors },
                  { key: "open", header: t("nvs.col.open"), align: "right", render: (a) => num(a.open_reports), sort: (a) => a.open_reports },
                  { key: "spend", header: t("nvs.col.spend"), align: "right", render: (a) => inr(a.spend), sort: (a) => toNumber(a.spend) },
                  { key: "need", header: t("nvs.col.need_rank"), align: "right", render: (a) => num(a.need_rank, 1) ?? <span className="text-muted" title={t("nvs.no_electors")}>—</span>, sort: (a) => toNumber(a.need_rank) },
                  { key: "spendr", header: t("nvs.col.spend_rank"), align: "right", render: (a) => num(a.spend_rank, 1) ?? <span className="text-muted" title={t("nvs.no_electors")}>—</span>, sort: (a) => toNumber(a.spend_rank) },
                  { key: "gap", header: t("nvs.col.gap"), align: "right", render: (a) => num(a.rank_gap, 1) ?? <span className="text-muted" title={t("nvs.no_electors")}>—</span>, sort: (a) => toNumber(a.rank_gap) },
                ]} />
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

export function SimpleTable({ head, rows }: { head: string[]; rows: (string | null | undefined)[][] }) {
  return (
    <table className="w-full text-note">
      <thead><tr>{head.map((h, i) => <th key={h + i} className={`border-b border-line py-1.5 font-normal text-muted ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-divider">
            {r.map((c, j) => <td key={j} className={`py-1.5 ${j ? "num" : ""}`}>{c ?? <span className="text-muted">—</span>}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
