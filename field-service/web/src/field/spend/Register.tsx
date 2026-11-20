/** Election register (LLD slice 4): window setup, ceiling gauge and running total, register by
 * statutory head (unmapped shown, never dropped), the head mapping, the review list with the
 * treasurer's ceiling decision, and the logged export. Finance scope "all" only. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Async, ErrorState, Field, KpiCard, KpiRow, Page, Panel, PillTabs } from "@/components/Layout";
import { Amount, Chip, Dot, FlagChips, Missing } from "@/components/Values";
import { Gauge } from "@/components/charts";
import { DataTable } from "@/components/DataTable";
import { ApiError, useApi } from "@/lib/api";
import { date, inr, num, pct, todayIst } from "@/lib/format";
import { bucketColor } from "@/lib/palette";
import { useSession, acLabel } from "@/lib/session";
import { useNamer, type Money } from "../data";
import { ExportDialog } from "./Ledger";

interface Window { window_id: number; starts_on: string; ends_on: string; ceiling_amount: Money | null; cash_limit: Money | null; event_id: string | null }
interface Reg {
  window: Window; heads: { statutory_head: string; mapped: boolean; entries: number; amount: Money }[];
  total: Money; entries: number; counted: Money; ceiling_used_pct: Money | null; undecided: number;
  recorded_before_window: { entries: number; amount: Money }; daily: { date: string; counted_to_date: Money }[];
}
interface ReviewItem { txn_id: string; txn_no: string; txn_date: string; amount: Money; pay_mode: string | null; flagged: string[];
  counts_to_ceiling: boolean | null; in_election_window: boolean; top_bucket_code: string; bucket_name_hi: string; payer_name: string | null;
  statutory_head: string | null; remark: string | null }
interface HeadRow { bucket_id: number; code: string; name_hi: string; name_en: string; parent_id: number | null; statutory_head: string | null; effective_head: string | null }

export function Register() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const api = useApi();
  const qc = useQueryClient();
  const windows = useQuery({ queryKey: ["windows", s.userId], queryFn: () => api.get<{ windows: Window[] }>("/election-windows") });
  const [wid, setWid] = useState<number | null>(null);
  const [tab, setTab] = useState<"register" | "review" | "heads" | "setup">("register");
  const [exporting, setExporting] = useState(false);
  useEffect(() => {
    const ws = windows.data?.windows ?? [];
    if (wid === null && ws.length) {
      const today = todayIst();
      setWid((ws.find((w) => w.starts_on <= today && today <= w.ends_on) ?? ws[ws.length - 1]).window_id);
    }
    if (!ws.length && windows.data) setTab("setup");
  }, [windows.data, wid]);
  const reg = useQuery({ queryKey: ["register", s.userId, wid], enabled: wid !== null,
    queryFn: () => api.get<Reg>("/spend/register", { window_id: wid }) });
  const w = reg.data?.window;

  return (
    <Page title={t("nav.field.register")} qualifier={`${s.acNumber} · ${acLabel(s)}`}
      actions={<>
        {windows.data?.windows.length ? (
          <select className="em-input" aria-label={t("register.window")} value={wid ?? ""} onChange={(e) => setWid(Number(e.target.value))} data-testid="window-select">
            {windows.data.windows.map((x) => <option key={x.window_id} value={x.window_id}>{date(x.starts_on, i18n.language)} – {date(x.ends_on, i18n.language)}</option>)}
          </select>
        ) : null}
        {wid !== null ? <button type="button" className="em-btn" onClick={() => setExporting(true)}>{t("common.export_csv")}</button> : null}
      </>}>
      {w && reg.data ? (
        <KpiRow>
          <KpiCard testId="kpi-ceiling-used" label={t("register.used")} value={pct(reg.data.ceiling_used_pct)}
            missingReason={t("register.no_ceiling")}
            sub={<div className="mt-2"><Gauge usedPct={reg.data.ceiling_used_pct === null ? null : Number(reg.data.ceiling_used_pct)} label={t("register.used")} />
              <div className="mt-1">{t("register.counted_of", { counted: inr(reg.data.counted), ceiling: inr(w.ceiling_amount) ?? "—" })}</div></div>} />
          <KpiCard label={t("register.total")} value={inr(reg.data.total)} sub={t("register.total_sub", { n: num(reg.data.entries) })} />
          <KpiCard label={t("register.undecided")} value={num(reg.data.undecided)} sub={t("register.undecided_sub")} />
          <KpiCard label={t("register.cash_limit")} value={inr(w.cash_limit)} missingReason={t("register.no_cash_limit")} sub={t("register.cash_limit_sub")} />
        </KpiRow>
      ) : null}
      {reg.data && reg.data.recorded_before_window.entries ? (
        <p role="note" className="text-note text-muted" data-testid="before-window">
          {t("register.before_window", { n: reg.data.recorded_before_window.entries, amount: inr(reg.data.recorded_before_window.amount) })}
        </p>
      ) : null}
      <PillTabs label={t("register.tabs")} value={tab} onChange={setTab}
        items={[{ id: "register", label: t("register.tab_register") }, { id: "review", label: t("register.tab_review") },
          { id: "heads", label: t("register.tab_heads") }, { id: "setup", label: t("register.tab_setup") }]} />

      {tab === "register" ? (
        <Panel title={t("register.by_head")} subtitle={t("register.by_head_sub")}>
          {wid === null ? <p className="text-note text-muted">{t("register.no_window")}</p> : (
            <Async q={reg} empty={(d) => !d.heads.length}>
              {(d) => (
                <>
                  <DataTable filters={false} rowKey={(h) => h.statutory_head} rows={d.heads} testId="register-heads"
                    columns={[
                      { key: "head", header: t("register.head"), render: (h) => h.mapped ? h.statutory_head
                        : <span className="inline-flex items-center gap-2"><Chip tone="warn" icon="?">{t("register.unmapped")}</Chip><span className="text-note text-muted">{t("register.unmapped_hint")}</span></span>,
                        sort: (h) => h.statutory_head },
                      { key: "n", header: t("register.entries"), align: "right", render: (h) => num(h.entries), sort: (h) => h.entries },
                      { key: "amount", header: t("register.amount"), align: "right", render: (h) => <Amount value={h.amount} />, sort: (h) => Number(h.amount) },
                    ]} />
                  {d.daily.length ? (
                    <div className="mt-5">
                      <h3 className="mb-2 text-body font-semibold">{t("register.running")}</h3>
                      <ol className="grid gap-x-10 gap-y-1 text-note sm:grid-cols-2 lg:grid-cols-3">
                        {d.daily.map((x) => <li key={x.date} className="flex justify-between border-b border-divider py-1"><span>{date(x.date, i18n.language)}</span><span className="num">{inr(x.counted_to_date)}</span></li>)}
                      </ol>
                    </div>
                  ) : null}
                </>
              )}
            </Async>
          )}
        </Panel>
      ) : tab === "review" ? <Review wid={wid} /> : tab === "heads" ? <Heads /> : <Setup windows={windows.data?.windows ?? []} onSaved={(id) => { setWid(id); setTab("register"); void qc.invalidateQueries(); }} />}
      {exporting && wid !== null ? <ExportDialog onClose={() => setExporting(false)}
        run={(reason) => api.download("/spend/register/export.csv", { window_id: wid, reason }, `register-${s.acNumber}-${wid}.csv`)} /> : null}
    </Page>
  );
}

function Review({ wid }: { wid: number | null }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const s = useSession();
  const qc = useQueryClient();
  const name = useNamer();
  const q = useQuery({ queryKey: ["review", s.userId, wid], enabled: wid !== null,
    queryFn: () => api.get<{ items: ReviewItem[] }>("/spend/register/review", { window_id: wid }) });
  const [err, setErr] = useState<unknown>(null);
  async function decide(id: string, counts: boolean | null) {
    setErr(null);
    try { await api.post(`/txns/${id}/ceiling`, { counts_to_ceiling: counts }); await qc.invalidateQueries(); } catch (e) { setErr(e); }
  }
  return (
    <Panel title={t("register.review")} subtitle={t("register.review_sub")} testId="register-review">
      {err ? <ErrorState error={err} /> : null}
      <Async q={q} empty={(d) => !d.items.length}>
        {(d) => (
          <DataTable rowKey={(r) => r.txn_id} rows={d.items} initialSort={[{ key: "date", dir: 1 }]}
            columns={[
              { key: "no", header: t("register.no"), render: (r) => r.txn_no, filter: (r) => r.txn_no },
              { key: "date", header: t("entry.date"), render: (r) => date(r.txn_date, i18n.language), sort: (r) => r.txn_date },
              { key: "bucket", header: t("entry.bucket"), render: (r) => <span className="inline-flex items-center gap-2"><Dot color={bucketColor(r.top_bucket_code)} />{name({ name_hi: r.bucket_name_hi, name_en: r.bucket_name_hi })}</span>, filter: (r) => r.bucket_name_hi },
              { key: "payer", header: t("entry.paid_by"), render: (r) => r.payer_name ?? <Missing />, filter: (r) => r.payer_name },
              { key: "flags", header: t("ledger.flag"), render: (r) => <FlagChips flags={r.flagged.filter((f) => f !== "large")} /> },
              { key: "amount", header: t("register.amount"), align: "right", render: (r) => <Amount value={r.amount} />, sort: (r) => Number(r.amount) },
              { key: "ceiling", header: t("entry.ceiling"), render: (r) => r.in_election_window ? (
                <span className="inline-flex gap-1" data-testid={`ceiling-${r.txn_no}`}>
                  {([true, false] as const).map((v) => (
                    <button key={String(v)} type="button" className={r.counts_to_ceiling === v ? "em-btn-primary" : "em-btn"} aria-pressed={r.counts_to_ceiling === v}
                      onClick={() => decide(r.txn_id, r.counts_to_ceiling === v ? null : v)}>{v ? t("register.counts_yes") : t("register.counts_no")}</button>
                  ))}
                </span>
              ) : <span className="text-note text-muted">{t("register.before_window_short")}</span> },
            ]} />
        )}
      </Async>
    </Panel>
  );
}

function Heads() {
  const { t } = useTranslation();
  const api = useApi();
  const s = useSession();
  const qc = useQueryClient();
  const name = useNamer();
  const q = useQuery({ queryKey: ["heads", s.userId], queryFn: () => api.get<{ buckets: HeadRow[] }>("/statutory-heads") });
  const [edit, setEdit] = useState<Record<number, string>>({});
  const [err, setErr] = useState<unknown>(null);
  const admin = s.role === "admin";
  async function save(id: number) {
    setErr(null);
    try { await api.put(`/buckets/${id}/statutory-head`, { statutory_head: (edit[id] ?? "").trim() || null }); await qc.invalidateQueries(); }
    catch (e) { setErr(e); }
  }
  return (
    <Panel title={t("register.heads")} subtitle={t("register.heads_sub")} footnote={admin ? t("register.heads_shared") : t("register.heads_admin_only")}>
      {err ? <ErrorState error={err} /> : null}
      <Async q={q} empty={(d) => !d.buckets.length}>
        {(d) => (
          <ul className="flex flex-col">
            {d.buckets.filter((b) => b.parent_id === null || b.statutory_head).map((b) => (
              <li key={b.bucket_id} className="grid grid-cols-[minmax(10rem,16rem)_1fr_auto] items-center gap-3 border-t border-divider py-2">
                <span className="inline-flex items-center gap-2"><Dot color={bucketColor(b.code)} />{name(b)}{b.parent_id !== null ? <span className="text-note text-muted"> ({b.code})</span> : null}</span>
                {admin ? (
                  <input className="em-input" placeholder={t("register.unmapped")} value={edit[b.bucket_id] ?? b.statutory_head ?? ""}
                    onChange={(e) => setEdit((x) => ({ ...x, [b.bucket_id]: e.target.value }))} aria-label={`${t("register.head")}: ${name(b)}`} />
                ) : <span>{b.statutory_head ?? <Chip tone="warn" icon="?">{t("register.unmapped")}</Chip>}</span>}
                {admin ? <button type="button" className="em-btn" disabled={edit[b.bucket_id] === undefined} onClick={() => save(b.bucket_id)}>{t("common.save")}</button> : <span />}
              </li>
            ))}
          </ul>
        )}
      </Async>
    </Panel>
  );
}

function Setup({ windows, onSaved }: { windows: Window[]; onSaved: (id: number) => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const [editing, setEditing] = useState<Window | null>(null);
  const blank = { starts_on: todayIst(), ends_on: todayIst(), ceiling_amount: "", cash_limit: "" };
  const [v, setV] = useState(blank);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setV(editing ? { starts_on: editing.starts_on, ends_on: editing.ends_on, ceiling_amount: editing.ceiling_amount ?? "", cash_limit: editing.cash_limit ?? "" } : blank);
  }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    const body = { starts_on: v.starts_on, ends_on: v.ends_on, ceiling_amount: v.ceiling_amount || null, cash_limit: v.cash_limit || null };
    try {
      const r = editing ? await api.put<Window>(`/election-windows/${editing.window_id}`, body) : await api.post<Window>("/election-windows", body);
      setEditing(null); onSaved(r.window_id);
    } catch (x) { setErr(x); }
  }
  return (
    <Panel title={t("register.setup")} subtitle={t("register.setup_sub")}>
      {windows.length ? (
        <ul className="mb-5 flex flex-col">
          {windows.map((w) => (
            <li key={w.window_id} className="flex flex-wrap items-center gap-4 border-t border-divider py-2">
              <span className="font-semibold">{date(w.starts_on, i18n.language)} – {date(w.ends_on, i18n.language)}</span>
              <span className="text-note text-muted">{t("register.ceiling")} {inr(w.ceiling_amount) ?? "—"} · {t("register.cash_limit")} {inr(w.cash_limit) ?? "—"}</span>
              <button type="button" className="em-btn ml-auto" onClick={() => setEditing(w)}>{t("register.edit")}</button>
            </li>
          ))}
        </ul>
      ) : <p className="mb-4 text-note text-muted">{t("register.no_window")}</p>}
      <form className="grid max-w-3xl gap-3 md:grid-cols-4" onSubmit={submit} data-testid="window-form">
        <Field label={t("register.starts")}><input type="date" className="em-input" required value={v.starts_on} onChange={(e) => setV({ ...v, starts_on: e.target.value })} /></Field>
        <Field label={t("register.ends")}><input type="date" className="em-input" required value={v.ends_on} onChange={(e) => setV({ ...v, ends_on: e.target.value })} /></Field>
        <Field label={t("register.ceiling")}><input className="em-input" inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={v.ceiling_amount} onChange={(e) => setV({ ...v, ceiling_amount: e.target.value })} /></Field>
        <Field label={t("register.cash_limit")}><input className="em-input" inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={v.cash_limit} onChange={(e) => setV({ ...v, cash_limit: e.target.value })} /></Field>
        <p className="text-note text-muted md:col-span-4">{t("register.setup_note")}</p>
        {err ? <div className="md:col-span-4"><ErrorState error={err} />{err instanceof ApiError ? <p className="text-note text-muted">{err.detail}</p> : null}</div> : null}
        <div className="flex gap-2 md:col-span-4">
          <button type="submit" className="em-btn-primary">{editing ? t("common.save") : t("register.create")}</button>
          {editing ? <button type="button" className="em-btn" onClick={() => setEditing(null)}>{t("common.cancel")}</button> : null}
        </div>
      </form>
    </Panel>
  );
}
