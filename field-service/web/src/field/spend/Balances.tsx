/** Team balances: disputed and overdue items first; the team as a tree, each member with a stacked bar
 * (spent, passed on, returned, balance) and chips; and the money flow from the candidate through the
 * team into buckets. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Async, Page, Panel } from "@/components/Layout";
import { Chip, Dot, FlagChips, Missing, StatusChip } from "@/components/Values";
import { ChartFrame, Flow } from "@/components/charts";
import { useApi } from "@/lib/api";
import { date, inr, num, toNumber } from "@/lib/format";
import { bucketColor, slot } from "@/lib/palette";
import { useSession, acLabel } from "@/lib/session";
import { useBalances, useBucketIndex, useNamer, useSpendFilters, type BalanceRow, type Txn } from "../data";
import { EntryDrawer, KIND_ICON } from "./Ledger";
import { SimpleTable } from "./SpendOverview";
import { SpendFilterRow } from "./Filters";

const PARTS = [
  { key: "spent", color: slot(6) }, { key: "passed_on", color: slot(2) },
  { key: "returned", color: slot(4) }, { key: "balance", color: slot(8) },
] as const;

interface FlowData {
  nodes: { id: string; kind: "fund" | "member" | "bucket"; name?: string; fund_source?: string; code?: string }[];
  links: { source: string; target: string; value: string; kind: string }[];
}

export function Balances() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const api = useApi();
  const name = useNamer();
  const buckets = useBucketIndex();
  const { params } = useSpendFilters();
  const bal = useBalances();
  const [open, setOpen] = useState<Txn | null>(null);
  const attention = useQuery({
    queryKey: ["attention", s.userId],
    queryFn: async () => {
      const [disputed, pending] = await Promise.all([
        api.get<{ items: Txn[] }>("/txns", { status: "disputed", limit: 200 }),
        api.get<{ items: Txn[] }>("/txns", { status: "pending_ack", limit: 200 }),
      ]);
      const rank = (x: Txn) => (x.status === "disputed" ? 0 : x.flagged.includes("ack_overdue") ? 1 : 2);
      return [...disputed.items, ...pending.items].sort((a, b) => rank(a) - rank(b) || a.txn_date.localeCompare(b.txn_date));
    },
  });
  const flow = useQuery({ queryKey: ["flow", s.userId, params], queryFn: () => api.get<FlowData>("/spend/flow", params) });

  const flowView = (() => {
    const d = flow.data;
    if (!d) return null;
    const idx = new Map(d.nodes.map((n, i) => [n.id, i]));
    const nodes = d.nodes.map((n) => ({
      name: n.kind === "fund" ? t(`fund_source.${n.fund_source}`) : n.kind === "bucket" ? name(buckets.byCode.get(n.code ?? ""), n.name) : n.name ?? "",
      color: n.kind === "bucket" ? bucketColor(n.code) : n.kind === "fund" ? "var(--em-muted)" : "var(--em-accent)",
    }));
    const links = d.links.map((l) => ({ source: idx.get(l.source)!, target: idx.get(l.target)!, value: toNumber(l.value) ?? 0 }));
    return { nodes, links, raw: d };
  })();

  return (
    <Page title={t("nav.field.balances")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <Panel testId="attention" title={t("balances.attention")} subtitle={t("balances.attention_sub")}>
        <Async q={attention} empty={(d) => !d.length}>
          {(items) => (
            <ul className="flex flex-col">
              {items.map((x) => (
                <li key={x.txn_id}>
                  <button type="button" onClick={() => setOpen(x)} className="grid w-full grid-cols-[1.25rem_7rem_1fr_auto_7rem] items-center gap-3 border-t border-divider px-1 py-2 text-left hover:bg-raised">
                    <span aria-hidden className="text-ink-2">{KIND_ICON[x.kind]}</span>
                    <span className="text-note text-muted">{x.txn_no}</span>
                    <span>{x.from_name} → {x.to_name} <span className="text-note text-muted">· {date(x.txn_date, i18n.language)}</span></span>
                    <span className="flex gap-1"><StatusChip status={x.status} /><FlagChips flags={x.flagged.filter((f) => f === "ack_overdue")} /></span>
                    <span className="num">{inr(x.amount)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Panel>

      <Panel title={t("balances.tree")} subtitle={t("balances.tree_sub")}>
        <div className="mb-3 flex flex-wrap gap-4 text-label text-muted">
          {PARTS.map((p) => <span key={p.key} className="inline-flex items-center gap-1.5"><Dot color={p.color} />{t(`balances.part.${p.key}`)}</span>)}
        </div>
        <Async q={bal} empty={(d) => !d.members.length}>
          {(d) => <ul className="flex flex-col" data-testid="balance-tree">{d.members.map((m) => <MemberRow key={m.member_id} m={m} />)}</ul>}
        </Async>
      </Panel>

      <Panel title={t("balances.flow")} subtitle={t("balances.flow_sub")}>
        <div className="mb-3"><SpendFilterRow withMember={false} /></div>
        <Async q={flow} empty={(d) => !d.links.length}>
          {() => flowView ? (
            <ChartFrame height={Math.max(320, flowView.nodes.length * 28)}
              table={<SimpleTable head={[t("balances.from"), t("balances.to"), t("spend.col.spend")]}
                rows={flowView.raw.links.map((l) => [flowView.nodes[flowView.raw.nodes.findIndex((n) => n.id === l.source)].name,
                  flowView.nodes[flowView.raw.nodes.findIndex((n) => n.id === l.target)].name, inr(l.value)])} />}>
              <Flow nodes={flowView.nodes} links={flowView.links} />
            </ChartFrame>
          ) : null}
        </Async>
      </Panel>
      <EntryDrawer txn={open} onClose={() => setOpen(null)} />
    </Page>
  );
}

function MemberRow({ m }: { m: BalanceRow }) {
  const { t, i18n } = useTranslation();
  // spent, passed on and returned are sums of positive amounts; the balance can be negative (the team
  // owes the member). The bar spans everything that went out plus any positive balance; a negative balance
  // is the part of the outflow beyond what was received, drawn hatched in the "bad" colour after a marker.
  const values = PARTS.map((p) => ({ ...p, v: toNumber(m[p.key]) ?? 0 }));
  const bal = m.balance === null ? 0 : toNumber(m.balance) ?? 0;
  const segments = values.map((p) => ({ ...p, v: Math.max(0, p.v) }));
  const whole = segments.reduce((a, p) => a + p.v, 0);
  const owedPct = bal < 0 && whole > 0 ? (-bal / whole) * 100 : 0;
  return (
    <li className="grid grid-cols-[minmax(12rem,18rem)_1fr_auto] items-center gap-4 border-t border-divider py-3" data-testid="balance-row">
      <div style={{ paddingLeft: m.depth * 20 }} className="min-w-0">
        <div className="truncate font-semibold">{m.depth ? <span aria-hidden className="text-muted">└ </span> : null}{m.display_name}</div>
        <div className="text-note text-muted">
          {t("balances.received")} {inr(m.received)} · {t("balances.last_entry")} {m.last_entry_date ? date(m.last_entry_date, i18n.language) : <Missing reason={t("balances.no_entries")} />}
          {" · "}{t("balances.lag")} {m.logging_lag_days !== null ? t("balances.lag_days", { n: num(m.logging_lag_days, 1) }) : <Missing reason={t("balances.no_entries")} />}
        </div>
      </div>
      <div>
        {whole > 0 ? (
          <div className="relative flex h-3 overflow-hidden rounded-full bg-raised" role="img" data-testid="balance-bar"
            aria-label={values.map((p) => `${t(`balances.part.${p.key}`)} ${inr(p.v)}`).join(", ")}>
            {segments.filter((p) => p.v > 0).map((p) => (
              <span key={p.key} title={`${t(`balances.part.${p.key}`)}: ${inr(p.v)}`} className="h-full border-r-2 border-surface last:border-r-0"
                style={{ width: `${(p.v / whole) * 100}%`, background: p.color }} />
            ))}
            {owedPct > 0 ? (
              <span data-testid="balance-owed" title={`${t("balances.owed_to_member")}: ${inr(-bal)}`}
                className="absolute inset-y-0 right-0 border-l-2 border-bad"
                style={{ width: `${owedPct}%`, background: "repeating-linear-gradient(135deg, var(--em-bad) 0 3px, transparent 3px 6px)" }} />
            ) : null}
          </div>
        ) : <span className="text-note text-muted">{t("balances.no_money")}</span>}
        <div className="mt-1 flex flex-wrap gap-x-4 text-label text-muted">
          {values.map((p) => <span key={p.key}>{t(`balances.part.${p.key}`)} <span className={`num ${p.key === "balance" && bal < 0 ? "text-bad" : "text-ink-2"}`}>{p.key === "balance" && m.balance === null ? "—" : inr(p.v)}</span></span>)}
          {bal < 0 ? <span className="text-bad">⚠ {t("balances.owed_to_member")} {inr(-bal)}</span> : null}
        </div>
      </div>
      <div className="flex flex-col items-end gap-1">
        <span className="num text-panel">{m.is_principal ? <span className="text-note text-muted">{t("balances.source")}</span> : inr(m.balance)}</span>
        <span className="flex flex-wrap justify-end gap-1">
          {(toNumber(m.in_transit) ?? 0) > 0 ? <Chip tone="warn" icon="⏳">{t("balances.in_transit")} {inr(m.in_transit)}</Chip> : null}
          {(toNumber(m.disputed) ?? 0) > 0 ? <Chip tone="bad" icon="⚠">{t("balances.disputed")} {inr(m.disputed)}</Chip> : null}
          {m.overdue_acks > 0 ? <Chip tone="serious" icon="⚑">{t("balances.overdue", { n: m.overdue_acks })}</Chip> : null}
        </span>
      </div>
    </li>
  );
}
