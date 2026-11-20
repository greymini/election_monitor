/** Ledger: a summary strip for the current filter, entries grouped by day (or bucket, or member) with
 * subtotals, an entry drawer (fields, receipt, links, follow-up, audit, void / reclassify), and the
 * logged CSV export. Only expenses count as spend; advances and returns are shown, never summed in. */
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Async, Drawer, ErrorState, Field, Page, Panel, PillTabs, Select } from "@/components/Layout";
import { Chip, Dot, FlagChips, MachineLabel, Missing, Provenance, StatusChip } from "@/components/Values";
import { Donut } from "@/components/charts";
import { useApi } from "@/lib/api";
import { date, dateTime, inr, num, toNumber } from "@/lib/format";
import { bucketColor } from "@/lib/palette";
import { atLeast, useSession, acLabel } from "@/lib/session";
import { useAreaIndex, useBucketIndex, useNamer, useSpendFilters, type Txn } from "../data";
import { SpendFilterRow } from "./Filters";

type Grouping = "day" | "bucket" | "member";
const KINDS = ["expense", "advance", "return", "inflow", "adjustment"] as const;
const STATUSES = ["posted", "pending_ack", "disputed", "void"] as const;
const FLAGS = ["large", "cash_over_limit", "sensitive_in_window", "backdated", "ack_overdue"] as const;

const PAGE = 100;

/** The ledger screen's figures over every row the filter matches (GET /txns/summary); the rows themselves
 * are paged for display only, so no figure depends on how many rows have been loaded. */
interface LedgerSummary {
  totals: { spend_total: string; spend_entries: number; rows: number };
  largest: { txn_id: string; txn_no: string; amount: string; top_bucket_code: string } | null;
  by_bucket: { top_bucket_code: string; spend: string; entries: number }[];
  groups: { key: string; rows: number; transfers: number; spend: string; member_name: string | null }[];
}

export const KIND_ICON: Record<string, string> = { expense: "₹", advance: "⇄", return: "↩", inflow: "⊕", adjustment: "±" };

function useLedgerData(filters: Record<string, string | null>, grouping: Grouping) {
  const s = useSession();
  const api = useApi();
  const { params } = useSpendFilters();
  const p = { ...params, ...filters };
  const summary = useQuery({ queryKey: ["txns-summary", s.userId, p, grouping],
    queryFn: () => api.get<LedgerSummary>("/txns/summary", { ...p, grouping }) });
  const rows = useInfiniteQuery({
    queryKey: ["txns", s.userId, p],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<{ items: Txn[]; total: number }>("/txns", { ...p, limit: PAGE, offset: pageParam }),
    getNextPageParam: (last, pages) => { const n = pages.reduce((a, x) => a + x.items.length, 0); return n < last.total ? n : undefined; },
  });
  return { summary, rows };
}

/** The group a row belongs to: the same rule as GET /txns/summary (pulse/ledger/queries.py). */
function groupKey(x: Txn, grouping: Grouping): string {
  if (grouping === "day") return x.txn_date;
  if (grouping === "bucket") return x.kind === "expense" ? x.top_bucket_code ?? "OTHER" : `~${x.kind}`;
  return (x.kind === "expense" ? x.from_member_id : x.from_member_id ?? x.to_member_id) ?? "";
}

export function Ledger() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const api = useApi();
  const name = useNamer();
  const buckets = useBucketIndex();
  const places = useAreaIndex();
  const { params } = useSpendFilters();
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [flag, setFlag] = useState("");
  const [grouping, setGrouping] = useState<Grouping>("day");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Txn | null>(null);
  const [exporting, setExporting] = useState(false);
  const { summary, rows: q } = useLedgerData({ kind: kind || null, status: status || null, flag: flag || null }, grouping);
  const sum = summary.data;
  const items = useMemo(() => (q.data?.pages ?? []).flatMap((pg) => pg.items), [q.data]);
  const totalRows = q.data?.pages[0]?.total ?? 0;
  const mini = useMemo(() => (sum?.by_bucket ?? []).map((b) => ({
    key: b.top_bucket_code, label: name(buckets.byCode.get(b.top_bucket_code), b.top_bucket_code),
    value: toNumber(b.spend) ?? 0, color: bucketColor(b.top_bucket_code) })), [sum, buckets.byCode, name]);

  const groups = useMemo(() => {
    const loaded = new Map<string, Txn[]>();
    for (const x of items) { const k = groupKey(x, grouping); loaded.set(k, [...(loaded.get(k) ?? []), x]); }
    const label = (g: LedgerSummary["groups"][number]) => grouping === "day" ? date(g.key, i18n.language) ?? g.key
      : grouping === "bucket" ? (g.key.startsWith("~") ? t(`kind.${g.key.slice(1)}_plural`) : name(buckets.byCode.get(g.key), g.key))
      : g.member_name ?? t("common.unknown");
    return (sum?.groups ?? []).map((g) => ({ ...g, label: label(g), loaded: loaded.get(g.key) ?? [],
      color: grouping === "bucket" && !g.key.startsWith("~") ? bucketColor(g.key) : undefined }));
  }, [items, sum, grouping, i18n.language, t, name, buckets.byCode]);

  const toggle = (k: string) => setClosed((c) => { const n = new Set(c); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  return (
    <Page title={t("nav.field.ledger")} qualifier={`${s.acNumber} · ${acLabel(s)}`}
      actions={atLeast(s, "all") ? <button type="button" className="em-btn" onClick={() => setExporting(true)} data-testid="export-open">{t("common.export_csv")}</button> : null}>
      <SpendFilterRow withMember={atLeast(s, "team")} />
      <div className="flex flex-wrap items-end gap-2">
        <Select label={t("ledger.kind")} value={kind} allLabel={t("common.all")} onChange={setKind} testId="filter-kind"
          options={KINDS.map((k) => ({ value: k, label: t(`kind.${k}`) }))} />
        <Select label={t("ledger.status")} value={status} allLabel={t("common.all")} onChange={setStatus}
          options={STATUSES.map((k) => ({ value: k, label: t(`txn_status.${k}`) }))} />
        <Select label={t("ledger.flag")} value={flag} allLabel={t("common.all")} onChange={setFlag}
          options={FLAGS.map((k) => ({ value: k, label: t(`flag.${k}`) }))} />
      </div>

      <Panel testId="ledger-summary">
        <div className="flex flex-wrap items-center gap-8">
          <Stat label={t("ledger.total_spend")} value={sum ? inr(sum.totals.spend_total) : null} testId="ledger-total" />
          <Stat label={t("ledger.entries")} value={sum ? num(sum.totals.rows) : null} sub={sum ? t("ledger.entries_sub", { n: num(sum.totals.spend_entries) }) : undefined} />
          <Stat label={t("ledger.largest")} value={sum?.largest ? inr(sum.largest.amount) : null}
            sub={sum?.largest ? `${sum.largest.txn_no} · ${name(buckets.byCode.get(sum.largest.top_bucket_code), sum.largest.top_bucket_code)}` : undefined} />
          <div className="flex items-center gap-3">
            <div style={{ width: 96, height: 96 }} aria-hidden={!mini.length}>{mini.length ? <Donut data={mini} size="sm" /> : null}</div>
            <ul className="text-label">
              {mini.slice(0, 4).map((m) => <li key={m.key} className="flex items-center gap-1.5"><Dot color={m.color} />{m.label} <span className="num ml-1 text-ink-2">{inr(m.value)}</span></li>)}
              {mini.length > 4 ? <li className="text-muted">{t("ledger.more_buckets", { n: mini.length - 4 })}</li> : null}
            </ul>
          </div>
        </div>
      </Panel>

      <div className="flex items-center justify-between gap-3">
        <PillTabs label={t("ledger.group_by")} value={grouping} onChange={setGrouping}
          items={(["day", "bucket", "member"] as Grouping[]).map((g) => ({ id: g, label: t(`ledger.by_${g}`) }))} />
        <div className="flex gap-3 text-note">
          <button type="button" className="em-link" onClick={() => setClosed(new Set())}>{t("ledger.expand_all")}</button>
          <button type="button" className="em-link" onClick={() => setClosed(new Set(groups.map((g) => g.key)))}>{t("ledger.collapse_all")}</button>
        </div>
      </div>

      <Async q={q} empty={(d) => !d.pages[0]?.items.length}>
        {() => (<>
          <div className="flex flex-col gap-3" data-testid="ledger-groups">
            {groups.map((g) => {
              const isClosed = closed.has(g.key);
              return (
                <section key={g.key} className="em-card overflow-hidden">
                  <button type="button" onClick={() => toggle(g.key)} aria-expanded={!isClosed}
                    className="flex w-full items-center gap-3 bg-raised px-4 py-2.5 text-left">
                    <span aria-hidden className="text-label text-muted">{isClosed ? "▸" : "▾"}</span>
                    {g.color ? <Dot color={g.color} /> : null}
                    <span className="font-semibold">{g.label}</span>
                    <span className="text-note text-muted">
                      {t("ledger.group_count", { n: g.rows })}{g.transfers ? ` · ${t("ledger.group_transfers", { n: g.transfers })}` : ""}
                      {g.loaded.length < g.rows ? ` · ${t("ledger.group_shown", { shown: num(g.loaded.length), n: num(g.rows) })}` : ""}
                    </span>
                    <span className="ml-auto text-note text-muted">{t("ledger.subtotal")}</span>
                    <span className="num w-28 font-semibold">{inr(g.spend)}</span>
                  </button>
                  {!isClosed && g.loaded.length ? (
                    <ul>
                      {g.loaded.map((x) => <Row key={x.txn_id} x={x} onOpen={() => setOpen(x)} areaName={x.area_id ? name(places.area.get(x.area_id), x.area_id) : null} />)}
                    </ul>
                  ) : null}
                </section>
              );
            })}
          </div>
          {q.hasNextPage ? (
            <div className="flex items-center justify-center gap-3 text-note text-muted">
              <span>{t("ledger.rows_shown", { shown: num(items.length), n: num(totalRows) })}</span>
              <button type="button" className="em-btn" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}
                data-testid="ledger-more">{t("ledger.show_more")}</button>
            </div>
          ) : null}
        </>)}
      </Async>
      <EntryDrawer txn={open} onClose={() => setOpen(null)} />
      {exporting ? <ExportDialog onClose={() => setExporting(false)} run={(reason) =>
        api.download("/txns/export.csv", { ...params, kind: kind || null, status: status || null, flag: flag || null, reason },
          `ledger-${s.acNumber}.csv`)} /> : null}
    </Page>
  );
}

function Stat({ label, value, sub, testId }: { label: string; value: string | null | undefined; sub?: string; testId?: string }) {
  return (
    <div data-testid={testId}>
      <div className="text-label uppercase text-muted">{label}</div>
      <div className="text-title">{value ?? <Missing />}</div>
      {sub ? <div className="text-note text-muted">{sub}</div> : null}
    </div>
  );
}

function Row({ x, onOpen, areaName }: { x: Txn; onOpen: () => void; areaName: string | null }) {
  const { t } = useTranslation();
  const name = useNamer();
  const buckets = useBucketIndex();
  const isSpend = x.kind === "expense";
  const top = buckets.byCode.get(x.top_bucket_code ?? "");
  const sub = x.bucket_code && x.bucket_code.includes(".") ? buckets.byCode.get(x.bucket_code) : undefined;
  return (
    <li>
      <button type="button" onClick={onOpen} data-testid="ledger-row" data-kind={x.kind}
        className={`grid w-full grid-cols-[1.25rem_7rem_minmax(0,1fr)_auto_7.5rem] items-center gap-3 border-t border-divider px-4 py-2.5 text-left hover:bg-raised ${x.status === "void" ? "opacity-60" : ""} ${!isSpend ? "bg-[color-mix(in_srgb,var(--em-accent)_5%,transparent)]" : ""}`}>
        {isSpend ? <Dot color={bucketColor(x.top_bucket_code)} /> : <span aria-hidden className="text-center text-ink-2">{KIND_ICON[x.kind]}</span>}
        <span className="text-note text-muted">{x.txn_no}</span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {isSpend ? (
              <span className="text-body">
                {name(top, x.top_bucket_name_hi ?? "")}{sub ? <span className="text-ink-2"> › {name(sub)}</span> : null}
                {x.custom_label ? <span className="text-ink-2"> · {x.custom_label}</span> : null}
              </span>
            ) : (
              <span className="text-body">
                <Chip>{t(`kind.${x.kind}`)}</Chip>{" "}
                {x.from_name ?? t("ledger.outside")} <span aria-hidden>→</span> {x.to_name ?? t("ledger.outside")}
              </span>
            )}
            {x.suggest_source && x.suggest_source !== "user" ? <MachineLabel what={t("ledger.suggested_bucket")} /> : null}
            <FlagChips flags={x.flagged} />
          </span>
          <span className="block truncate text-note text-muted">
            {[isSpend ? x.from_name : null, areaName, x.remark].filter(Boolean).join(" · ")}
          </span>
        </span>
        <span>{x.status !== "posted" || !isSpend ? <StatusChip status={x.status} /> : null}</span>
        <span className={`num text-body ${isSpend ? "font-semibold" : "text-ink-2"} ${x.status === "void" ? "line-through" : ""}`}>
          {inr(x.amount)}
        </span>
      </button>
    </li>
  );
}

interface Detail {
  txn: Txn;
  audit: { at: string; action: string; old: Record<string, unknown> | null; new: Record<string, unknown> | null; note: string | null; actor_name: string | null }[];
  followup: { due_at: string; asked_at: string | null; answer: string | null; answered_at: string | null; has_photo: boolean } | null;
  report: { report_id: string; report_no: string; title: string | null; status: string } | null;
  issue: { issue_id: string; issue_no: string; title: string; status: string } | null;
  receipt: { asset_id: string; status: string; url?: string; mime: string | null } | null;
}

export function EntryDrawer({ txn, onClose }: { txn: Txn | null; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const s = useSession();
  const qc = useQueryClient();
  const name = useNamer();
  const buckets = useBucketIndex();
  const places = useAreaIndex();
  const q = useQuery({ queryKey: ["txn", s.userId, txn?.txn_id], enabled: !!txn,
    queryFn: () => api.get<Detail>(`/txns/${txn!.txn_id}`) });
  const [mode, setMode] = useState<"" | "void" | "reclassify">("");
  const [reason, setReason] = useState("");
  const [bucket, setBucket] = useState("");
  const [err, setErr] = useState<unknown>(null);

  async function act(path: string, body: unknown) {
    setErr(null);
    try {
      await api.post(path, body);
      setMode(""); setReason("");
      await qc.invalidateQueries();
    } catch (e) { setErr(e); }
  }
  const d = q.data;
  const x = d?.txn ?? txn;
  return (
    <Drawer open={!!txn} onClose={() => { setMode(""); setErr(null); onClose(); }} wide
      title={x ? `${x.txn_no} · ${t(`kind.${x.kind}`)}` : ""}>
      {x ? (
        <div className="flex flex-col gap-5" data-testid="entry-drawer">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-kpi">{inr(x.amount)}</span>
            <StatusChip status={x.status} />
            <FlagChips flags={x.flagged} />
          </div>
          <Provenance by={x.created_by_name} via={x.created_via} at={dateTime(x.created_at, i18n.language)} />
          <dl className="grid grid-cols-[11rem_1fr] gap-x-4 gap-y-2 text-body">
            <Def k={t("entry.date")} v={date(x.txn_date, i18n.language)} />
            {x.kind === "expense" ? (
              <>
                <Def k={t("entry.bucket")} v={<span className="inline-flex items-center gap-2"><Dot color={bucketColor(x.top_bucket_code)} />
                  {name(buckets.byCode.get(x.top_bucket_code ?? ""), x.top_bucket_name_hi ?? "")}
                  {x.bucket_code?.includes(".") ? ` › ${name(buckets.byCode.get(x.bucket_code))}` : ""}
                  {x.suggest_source && x.suggest_source !== "user" ? <MachineLabel what={t("ledger.suggested_bucket")} /> : null}</span>} />
                <Def k={t("entry.custom_label")} v={x.custom_label} />
                <Def k={t("entry.paid_by")} v={x.from_name} />
              </>
            ) : (
              <Def k={t("entry.from_to")} v={`${x.from_name ?? t("ledger.outside")} → ${x.to_name ?? t("ledger.outside")}`} />
            )}
            <Def k={t("entry.area")} v={x.area_id ? name(places.area.get(x.area_id), x.area_id) : x.block_id ? name(places.block.get(x.block_id), x.block_id) : null} />
            <Def k={t("entry.pay_mode")} v={x.pay_mode ? t(`pay_mode.${x.pay_mode}`) : null} />
            <Def k={t("entry.fund_source")} v={x.fund_source ? t(`fund_source.${x.fund_source}`) : null} />
            <Def k={t("entry.payee")} v={[x.payee_kind ? t(`payee.${x.payee_kind}`) : null, x.payee_label].filter(Boolean).join(" · ") || null} />
            <Def k={t("entry.reach")} v={x.reach_count !== null ? `${num(x.reach_count)} ${t(`reach.${x.reach_unit}`)}` : null} />
            <Def k={t("entry.remark")} v={x.remark} />
            <Def k={t("entry.window")} v={x.in_election_window ? t("common.yes") : t("common.no")} />
            {x.in_election_window ? <Def k={t("entry.ceiling")} v={x.counts_to_ceiling === null ? t("entry.undecided") : x.counts_to_ceiling ? t("common.yes") : t("common.no")} /> : null}
            {x.status === "void" ? <Def k={t("entry.void_reason")} v={x.void_reason} /> : null}
          </dl>
          {q.isLoading ? null : q.error ? <ErrorState error={q.error} /> : d ? (
            <>
              <Section title={t("entry.receipt")}>
                {d.receipt?.url ? <img src={d.receipt.url} alt={t("entry.receipt")} className="max-h-72 rounded-ctl border border-line" />
                  : d.receipt ? <p className="text-note text-muted">{t("entry.receipt_status", { status: d.receipt.status })}</p>
                  : <p className="text-note text-muted">{t("entry.no_receipt")}</p>}
              </Section>
              {d.report || d.issue ? (
                <Section title={t("entry.linked")}>
                  <ul className="flex flex-col gap-1 text-body">
                    {d.report ? <li><Link className="em-link" to={`/field/reports?open=${d.report.report_id}`}>{d.report.report_no}</Link> {d.report.title} <StatusChip status={d.report.status} kind="report" /></li> : null}
                    {d.issue ? <li><Link className="em-link" to={`/field/issues?open=${d.issue.issue_id}`}>{d.issue.issue_no}</Link> {d.issue.title} <StatusChip status={d.issue.status} kind="issue" /></li> : null}
                  </ul>
                </Section>
              ) : null}
              {d.followup ? (
                <Section title={t("entry.followup")}>
                  <p className="text-body" data-testid="followup-outcome">
                    {d.followup.answer ? <>{t(`followup.${d.followup.answer}`)} <span className="text-note text-muted">· {dateTime(d.followup.answered_at, i18n.language)}</span></>
                      : d.followup.asked_at ? t("entry.followup_asked", { at: dateTime(d.followup.asked_at, i18n.language) })
                      : t("entry.followup_due", { at: dateTime(d.followup.due_at, i18n.language) })}
                  </p>
                </Section>
              ) : null}
              <Section title={t("entry.audit")}>
                {d.audit.length ? (
                  <ol className="flex flex-col gap-1 text-note">
                    {d.audit.map((a, i) => (
                      <li key={i}><span className="text-muted">{dateTime(a.at, i18n.language)}</span> · {t(`audit.${a.action}`)} · {a.actor_name ?? t("common.system")}{a.note ? ` · ${a.note}` : ""}</li>
                    ))}
                  </ol>
                ) : <p className="text-note text-muted">{t("entry.no_audit")}</p>}
              </Section>
            </>
          ) : null}
          {x.status !== "void" ? (
            <Section title={t("entry.actions")}>
              {err ? <div className="mb-2"><ErrorState error={err} /><p className="mt-1 text-note text-muted">{(err as { detail?: string }).detail}</p></div> : null}
              {mode === "" ? (
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="em-btn" onClick={() => setMode("void")} data-testid="void-open">{t("entry.void")}</button>
                  {x.kind === "expense" ? <button type="button" className="em-btn" onClick={() => setMode("reclassify")}>{t("entry.reclassify")}</button> : null}
                  {x.in_election_window && x.kind === "expense" && atLeast(s, "all") ? (
                    <>
                      <button type="button" className="em-btn" onClick={() => act(`/txns/${x.txn_id}/ceiling`, { counts_to_ceiling: true })}>{t("register.counts_yes")}</button>
                      <button type="button" className="em-btn" onClick={() => act(`/txns/${x.txn_id}/ceiling`, { counts_to_ceiling: false })}>{t("register.counts_no")}</button>
                    </>
                  ) : null}
                </div>
              ) : mode === "void" ? (
                <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(`/txns/${x.txn_id}/void`, { reason: reason || null }); }}>
                  <Field label={t("entry.void_reason_label")}><input className="em-input" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} /></Field>
                  <p className="text-note text-muted">{t("entry.void_hint")}</p>
                  <div className="flex gap-2"><button type="submit" className="em-btn-primary">{t("entry.void_confirm")}</button><button type="button" className="em-btn" onClick={() => setMode("")}>{t("common.cancel")}</button></div>
                </form>
              ) : (
                <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(`/txns/${x.txn_id}/reclassify`, { bucket_id: Number(bucket) }); }}>
                  <Field label={t("entry.new_bucket")}>
                    <select className="em-input" value={bucket} onChange={(e) => setBucket(e.target.value)} required>
                      <option value="" />
                      {buckets.tops.map((b) => (
                        <optgroup key={b.bucket_id} label={name(b)}>
                          <option value={b.bucket_id}>{name(b)}</option>
                          {b.subheads.map((sh) => <option key={sh.bucket_id} value={sh.bucket_id}>{name(b)} › {name(sh)}</option>)}
                        </optgroup>
                      ))}
                    </select>
                  </Field>
                  <div className="flex gap-2"><button type="submit" className="em-btn-primary">{t("common.save")}</button><button type="button" className="em-btn" onClick={() => setMode("")}>{t("common.cancel")}</button></div>
                </form>
              )}
            </Section>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

function Def({ k, v }: { k: string; v: React.ReactNode | null | undefined }) {
  return (<><dt className="text-note text-muted">{k}</dt><dd>{v === null || v === undefined || v === "" ? <Missing /> : v}</dd></>);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-2 text-label uppercase text-muted">{title}</h3>{children}</section>;
}

/** CSV export asks for a reason first; the API logs it with the filters. */
export function ExportDialog({ onClose, run }: { onClose: () => void; run: (reason: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Drawer open title={t("common.export_csv")} onClose={onClose}>
      <form className="flex flex-col gap-3" data-testid="export-dialog" onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setErr(null);
        try { await run(reason.trim()); onClose(); } catch (x) { setErr(x); } finally { setBusy(false); }
      }}>
        <p className="text-body">{t("common.export_reason")}</p>
        <Field label={t("common.export_reason_label")}>
          <input className="em-input" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} maxLength={500} data-testid="export-reason" />
        </Field>
        {err ? <ErrorState error={err} /> : null}
        <div className="flex gap-2">
          <button type="submit" className="em-btn-primary" disabled={busy || reason.trim().length < 3}>{t("common.download")}</button>
          <button type="button" className="em-btn" onClick={onClose}>{t("common.cancel")}</button>
        </div>
      </form>
    </Drawer>
  );
}
