/** Chart wrappers. Every chart has a legend with values (or direct labels) so colour is never the only
 * signal, a hover tooltip, and a table view of the same numbers. Marks follow the dataviz rules: thin,
 * 2px surface gap between fills, recessive grid, one axis. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Bar, BarChart, CartesianGrid, Cell, Label, Pie, PieChart, ResponsiveContainer, Sankey, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { inr, inrShort, num } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { Dot } from "../Values";

export interface Slice { key: string; label: string; value: number; color: string; note?: string }

/** Panel body for a chart: the chart, its legend, and a "table" toggle. */
export function ChartFrame({ children, table, legend, height = 260, testId }: {
  children: React.ReactNode; table: React.ReactNode; legend?: React.ReactNode; height?: number; testId?: string;
}) {
  const { t } = useTranslation();
  const [asTable, setAsTable] = useState(false);
  return (
    <div data-testid={testId}>
      <div className="mb-2 flex justify-end">
        <button type="button" className="text-label text-muted underline underline-offset-2 hover:text-ink"
          aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? t("chart.show_chart") : t("chart.show_table")}
        </button>
      </div>
      {asTable ? table : <div style={{ height }}>{children}</div>}
      {!asTable && legend ? <div className="mt-3">{legend}</div> : null}
    </div>
  );
}

/** Legend with values: dot + label + value (+ share), optionally clickable. */
export function ValueLegend({ items, total, onSelect, selected, format = inr, columns = 2 }: {
  items: Slice[]; total?: number; onSelect?: (key: string) => void; selected?: string | null;
  format?: (v: number) => string | null; columns?: 1 | 2;
}) {
  return (
    <ul className={`grid gap-x-6 gap-y-1 text-note ${columns === 2 ? "sm:grid-cols-2" : ""}`}>
      {items.map((s) => {
        const share = total ? Math.round((s.value / total) * 1000) / 10 : null;
        const body = (
          <>
            <Dot color={s.color} />
            <span className="min-w-0 flex-1 truncate text-ink">{s.label}</span>
            <span className="num text-ink">{format(s.value)}</span>
            {share !== null ? <span className="num w-12 text-muted">{share.toLocaleString("en-IN")}%</span> : null}
          </>
        );
        return (
          <li key={s.key}>
            {onSelect ? (
              <button type="button" onClick={() => onSelect(s.key)} aria-pressed={selected === s.key}
                className={`flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-raised ${selected === s.key ? "bg-raised" : ""}`}>
                {body}
              </button>
            ) : <div className="flex items-center gap-2 px-1 py-0.5">{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}

function TipBox({ title, rows }: { title?: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-ctl border border-line bg-surface px-3 py-2 text-note shadow-lg">
      {title ? <div className="mb-1 font-semibold text-ink">{title}</div> : null}
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2">
          {r.color ? <Dot color={r.color} /> : null}
          <span className="text-ink-2">{r.label}</span>
          <span className="num ml-auto pl-3 text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Donut: slices separated by a 2px surface gap; centre shows the total; click a slice to select. */
export function Donut({ data, onSelect, centerLabel, size = "md", valueFormat }: {
  data: Slice[]; onSelect?: (key: string) => void; centerLabel?: string; size?: "sm" | "md";
  /** counts instead of money (default: rupees) */
  valueFormat?: (v: number) => string | null;
}) {
  const { color } = useTheme();
  const { i18n } = useTranslation();
  const total = data.reduce((a, s) => a + s.value, 0);
  const [inner, outer] = size === "sm" ? [38, 58] : [70, 105];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="label" innerRadius={inner} outerRadius={outer} paddingAngle={0}
          stroke={color("var(--em-surface)")} strokeWidth={2} isAnimationActive={false}
          onClick={onSelect ? (d: { key?: string; payload?: Slice }) => onSelect(d.payload?.key ?? d.key ?? "") : undefined}
          style={{ cursor: onSelect ? "pointer" : "default" }}>
          {data.map((s) => <Cell key={s.key} fill={color(s.color)} data-key={s.key} />)}
          <Label position="center" content={() => (
            <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central">
              <tspan x="50%" dy={size === "sm" ? 0 : "-0.5em"} fill={color("var(--em-text)")} fontSize={size === "sm" ? 13 : 20} fontWeight={700}>
                {valueFormat ? valueFormat(total) : inrShort(total, i18n.language)}
              </tspan>
              {size !== "sm" && centerLabel ? (
                <tspan x="50%" dy="1.6em" fill={color("var(--em-muted)")} fontSize={12}>{centerLabel}</tspan>
              ) : null}
            </text>
          )} />
        </Pie>
        <Tooltip content={({ payload }) => {
          const p = payload?.[0]?.payload as Slice | undefined;
          return p ? <TipBox rows={[{ label: p.label, value: `${(valueFormat ?? inr)(p.value)} · ${Math.round((p.value / (total || 1)) * 1000) / 10}%`, color: color(p.color) }]} /> : null;
        }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export interface Series { key: string; label: string; color: string }

/** Stacked vertical bars over time (or any category axis), one axis, money on y. */
export function StackedBars({ rows, series, xKey, xFormat, yFormat }: {
  rows: Record<string, number | string>[]; series: Series[]; xKey: string; xFormat?: (x: string) => string;
  /** counts instead of money: pass a formatter (default: rupees) */
  yFormat?: (v: number) => string;
}) {
  const { color } = useTheme();
  const { i18n } = useTranslation();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }} barCategoryGap="25%">
        <CartesianGrid vertical={false} stroke={color("var(--em-grid)")} />
        <XAxis dataKey={xKey} tickFormatter={xFormat} tick={{ fill: color("var(--em-muted)"), fontSize: 12 }}
          axisLine={{ stroke: color("var(--em-baseline)") }} tickLine={false} />
        <YAxis tickFormatter={(v) => (yFormat ? yFormat(v) : inrShort(v, i18n.language) ?? "")} allowDecimals={!yFormat} tick={{ fill: color("var(--em-muted)"), fontSize: 12 }}
          axisLine={false} tickLine={false} width={70} />
        <Tooltip cursor={{ fill: color("var(--em-raised)") }} content={({ payload, label }) => {
          if (!payload?.length) return null;
          const items = payload.filter((p) => Number(p.value) > 0).reverse();
          return <TipBox title={xFormat ? xFormat(String(label)) : String(label)}
            rows={items.map((p) => ({ label: series.find((s) => s.key === p.dataKey)?.label ?? String(p.dataKey), value: (yFormat ? yFormat(p.value as number) : inr(p.value as number)) ?? "", color: String(p.color) }))} />;
        }} />
        {series.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} stackId="a" fill={color(s.color)} stroke={color("var(--em-surface)")} strokeWidth={1}
            radius={i === series.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Horizontal bars as in the core's "Party position in the news": label left, track, value right.
 * Plain HTML, direct labels on every row, so no legend is needed. */
export function HBars({ items, format = inr, onSelect, max: maxIn, testId }: {
  items: { key: string; label: string; value: number; color?: string; sub?: string }[];
  format?: (v: number) => string | null; onSelect?: (key: string) => void; max?: number; testId?: string;
}) {
  const max = maxIn ?? Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="flex flex-col gap-2" data-testid={testId}>
      {items.map((it) => {
        const row = (
          <>
            <span className="w-40 shrink-0 truncate text-left text-body text-ink" title={it.label}>{it.label}</span>
            <span className="relative h-2.5 flex-1 rounded-full bg-raised" aria-hidden>
              <span className="absolute inset-y-0 left-0 rounded-full"
                style={{ width: `${Math.max(1, (it.value / max) * 100)}%`, background: it.color ?? "var(--em-seq-3)" }} />
            </span>
            <span className="num w-28 shrink-0 text-body text-ink">{format(it.value)}</span>
          </>
        );
        return (
          <li key={it.key}>
            {onSelect ? (
              <button type="button" className="flex w-full items-center gap-3 rounded hover:bg-raised" onClick={() => onSelect(it.key)}>{row}</button>
            ) : <div className="flex items-center gap-3">{row}</div>}
            {it.sub ? <div className="ml-[10.75rem] text-label text-muted">{it.sub}</div> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Paired bars: share of entries against share of value, per size band (tail analysis). */
export function PairedBars({ rows }: { rows: { key: string; label: string; a: number; b: number }[]; }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-4 text-label text-muted">
        <span className="inline-flex items-center gap-1.5"><Dot color="var(--em-seq-4)" />{t("chart.share_entries")}</span>
        <span className="inline-flex items-center gap-1.5"><Dot color="var(--em-seq-2)" />{t("chart.share_value")}</span>
      </div>
      {rows.map((r) => (
        <div key={r.key} className="grid grid-cols-[8rem_1fr_3.5rem] items-center gap-x-3 gap-y-1">
          <span className="row-span-2 text-note text-ink">{r.label}</span>
          <span className="h-2 rounded-full bg-raised"><span className="block h-2 rounded-full" style={{ width: `${r.a}%`, background: "var(--em-seq-4)" }} /></span>
          <span className="num text-label text-ink-2">{num(r.a, 1)}%</span>
          <span className="h-2 rounded-full bg-raised"><span className="block h-2 rounded-full" style={{ width: `${r.b}%`, background: "var(--em-seq-2)" }} /></span>
          <span className="num text-label text-ink-2">{num(r.b, 1)}%</span>
        </div>
      ))}
    </div>
  );
}

/** Scatter of two measures on their own axes (need versus spend); identities in the tooltip and the paired table. */
export function LabelledScatter({ points, xLabel, yLabel }: {
  points: { key: string; label: string; x: number; y: number }[]; xLabel: string; yLabel: string;
}) {
  const { color } = useTheme();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 16, right: 24, bottom: 28, left: 8 }}>
        <CartesianGrid stroke={color("var(--em-grid)")} />
        <XAxis type="number" dataKey="x" name={xLabel} tick={{ fill: color("var(--em-muted)"), fontSize: 12 }}
          axisLine={{ stroke: color("var(--em-baseline)") }} tickLine={false}
          label={{ value: xLabel, position: "insideBottom", offset: -16, fill: color("var(--em-muted)"), fontSize: 12 }} />
        <YAxis type="number" dataKey="y" name={yLabel} tick={{ fill: color("var(--em-muted)"), fontSize: 12 }}
          axisLine={false} tickLine={false} width={60}
          label={{ value: yLabel, angle: -90, position: "insideLeft", fill: color("var(--em-muted)"), fontSize: 12 }} />
        <ZAxis range={[90, 90]} />
        <Tooltip content={({ payload }) => {
          const p = payload?.[0]?.payload as { label: string; x: number; y: number } | undefined;
          return p ? <TipBox title={p.label} rows={[{ label: xLabel, value: num(p.x, 1) ?? "" }, { label: yLabel, value: num(p.y, 1) ?? "" }]} /> : null;
        }} />
        {/* no labels on the points: they collide where areas cluster; the table beside the chart names
            every point and the tooltip names the one under the cursor */}
        <Scatter data={points} fill={color("var(--em-seq-3)")} stroke={color("var(--em-surface)")} strokeWidth={2}
          isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export interface FlowNode { name: string; color: string }
export interface FlowLink { source: number; target: number; value: number }

/** Money flow (Sankey): funds -> candidate -> team -> buckets. Nodes are labelled with name and amount. */
export function Flow({ nodes, links }: { nodes: FlowNode[]; links: FlowLink[] }) {
  const { color } = useTheme();
  const { i18n } = useTranslation();
  if (!links.length) return null;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <Sankey data={{ nodes, links }} nodePadding={18} nodeWidth={10} margin={{ top: 8, right: 170, bottom: 8, left: 8 }}
        link={{ stroke: color("var(--em-seq-3)"), strokeOpacity: 0.25 }}
        node={(props: { x: number; y: number; width: number; height: number; index: number; payload: { name: string; value: number } }) => {
          const n = nodes[props.index];
          return (
            <g>
              <rect x={props.x} y={props.y} width={props.width} height={Math.max(2, props.height)} rx={2}
                fill={color(n.color)} />
              <text x={props.x + props.width + 6} y={props.y + props.height / 2} dominantBaseline="central"
                fontSize={12} fill={color("var(--em-text)")}>
                {props.payload.name}
                <tspan fill={color("var(--em-muted)")}> {inrShort(props.payload.value, i18n.language)}</tspan>
              </text>
            </g>
          );
        }}>
        <Tooltip content={({ payload }) => {
          const p = payload?.[0]?.payload as { payload?: { source?: { name: string }; target?: { name: string }; value?: number; name?: string } } | undefined;
          const d = p?.payload;
          if (!d) return null;
          const title = d.source && d.target ? `${d.source.name} → ${d.target.name}` : d.name;
          return <TipBox title={title} rows={[{ label: "₹", value: inr(d.value ?? 0) ?? "" }]} />;
        }} />
      </Sankey>
    </ResponsiveContainer>
  );
}

/** Ceiling gauge: a horizontal meter with the used share labelled; status colour + text. */
export function Gauge({ usedPct, label }: { usedPct: number | null; label: string }) {
  const tone = usedPct === null ? "var(--em-muted)" : usedPct >= 90 ? "var(--em-bad)" : usedPct >= 75 ? "var(--em-warn)" : "var(--em-ok)";
  const w = Math.min(100, Math.max(0, usedPct ?? 0));
  return (
    <div>
      <div className="relative h-4 rounded-full bg-raised" role="meter" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={usedPct ?? undefined} aria-label={label}>
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${w}%`, background: tone }} />
        {[75, 90].map((m) => <div key={m} className="absolute inset-y-0 w-px bg-muted" style={{ left: `${m}%` }} aria-hidden />)}
      </div>
    </div>
  );
}
